"""The database boundary the rest of the system stands on, checked against real PostgreSQL."""

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.database.roles import APP, MIGRATOR, WORKER, check_role
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support import database as harness
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

ACTOR = text("SELECT finvest_actor_kind(), finvest_actor_id()")


async def test_each_process_role_is_inside_its_boundary(
    app_database: Database, worker_database: Database, migrator_database: Database
) -> None:
    await check_role(app_database, APP)
    await check_role(worker_database, WORKER)
    await check_role(migrator_database, MIGRATOR)


async def test_a_role_is_refused_where_another_is_expected(app_database: Database) -> None:
    with pytest.raises(RuntimeError, match="expected 'finvest_worker'"):
        await check_role(app_database, WORKER)


def test_migrations_refuse_to_run_as_an_application_role(test_database: TestDatabase) -> None:
    result = harness.migrate(test_database, role="finvest_app")
    assert result.returncode != 0
    assert "Database role preflight failed" in result.stderr
    assert harness.PASSWORDS["finvest_app"] not in result.stdout + result.stderr


async def test_application_roles_cannot_create_or_see_migration_state(
    application_database: Database,
) -> None:
    for statement in (
        "CREATE TABLE escape (id int)",
        "SELECT * FROM alembic_version",
        "DELETE FROM alembic_version",
        "CREATE ROLE escape",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with application_database.transaction() as session:
                await session.execute(text(statement))


async def test_a_transaction_without_an_actor_has_none(app_database: Database) -> None:
    async with app_database.transaction() as session:
        assert (await session.execute(ACTOR)).one() == (None, None)


async def test_the_actor_is_visible_inside_its_transaction(app_database: Database) -> None:
    client = Actor("client", push_id())
    async with app_database.actor_transaction(client) as session:
        assert (await session.execute(ACTOR)).one() == ("client", client.id)
    async with app_database.actor_transaction(SYSTEM) as session:
        assert (await session.execute(ACTOR)).one() == ("system", None)


async def test_the_actor_never_leaks_to_the_next_user_of_a_connection(
    test_database: TestDatabase,
) -> None:
    # One connection in the pool, so both transactions are certain to share it.
    database = Database(test_database.settings("finvest_app", pool_size=1))
    try:
        async with database.actor_transaction(Actor("staff", push_id())) as session:
            first = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        async with database.transaction() as session:
            second = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            assert (await session.execute(ACTOR)).one() == (None, None)
        assert first == second

        with pytest.raises(RuntimeError, match="rolled back"):
            async with database.actor_transaction(Actor("client", push_id())) as session:
                raise RuntimeError("rolled back")
        async with database.transaction() as session:
            assert (await session.execute(ACTOR)).one() == (None, None)
    finally:
        await database.close()


@pytest.mark.parametrize("kind", ["admin", "", "CLIENT", "client "])
async def test_an_unrecognised_actor_kind_is_no_actor(app_database: Database, kind: str) -> None:
    async with app_database.transaction() as session:
        await session.execute(
            text("SELECT set_config('finvest.actor_kind', :kind, true)"), {"kind": kind}
        )
        assert (await session.execute(ACTOR)).one()[0] is None


@pytest.mark.parametrize("value", ["", "short", "a" * 21, "x' OR '1'='1", "a" * 19 + "\n"])
async def test_a_malformed_actor_id_is_no_actor(app_database: Database, value: str) -> None:
    async with app_database.transaction() as session:
        await session.execute(
            text("SELECT set_config('finvest.actor_id', :value, true)"), {"value": value}
        )
        assert (await session.execute(ACTOR)).one()[1] is None


async def test_the_push_id_type_accepts_generated_ids_only(app_database: Database) -> None:
    async with app_database.transaction() as session:
        value = push_id()
        assert (
            await session.execute(text("SELECT CAST(:value AS push_id)"), {"value": value})
        ).scalar_one() == value
    for bad in ("", "short", "a" * 21, "a" * 19 + "!"):
        with pytest.raises(DBAPIError, match="push_id_format"):
            async with app_database.transaction() as session:
                await session.execute(text("SELECT CAST(:value AS push_id)"), {"value": bad})


async def test_push_ids_sort_in_the_database_as_they_do_in_python(
    app_database: Database,
) -> None:
    # Mixed case and punctuation: a locale collation would order these differently.
    ids = ["-" * 20, "Z" * 20, "_" * 20, "a" * 20, "0" * 20, "A" * 20, "z" * 20]
    async with app_database.transaction() as session:
        rows = await session.execute(
            text("SELECT value FROM unnest(CAST(:ids AS push_id[])) AS value ORDER BY value"),
            {"ids": ids},
        )
        assert [row[0] for row in rows] == sorted(ids)


async def test_readiness_against_the_real_database(
    test_database: TestDatabase, app_database: Database
) -> None:
    app = create_app(test_database.settings("finvest_app"), app_database)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        assert (await client.get("/health/ready")).status_code == 200


async def test_readiness_fails_when_the_database_is_unreachable(
    test_database: TestDatabase,
) -> None:
    missing = TestDatabase(admin=test_database.admin, name="finvest_missing_database")
    database = Database(missing.settings("finvest_app"))
    try:
        assert not await database.ready()
    finally:
        await database.close()


async def test_new_functions_are_not_executable_until_granted(
    migrator_database: Database, application_database: Database
) -> None:
    async with migrator_database.transaction() as session:
        await session.execute(
            text("CREATE FUNCTION finvest_test_probe() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        )
    try:
        with pytest.raises(DBAPIError, match="permission denied"):
            async with application_database.transaction() as session:
                await session.execute(text("SELECT finvest_test_probe()"))
    finally:
        async with migrator_database.transaction() as session:
            await session.execute(text("DROP FUNCTION finvest_test_probe()"))


async def test_no_policy_trusts_the_system_actor_for_the_api_role(
    migrator_database: Database,
) -> None:
    # The actor settings are set by the connection itself, so finvest_app could claim to be the
    # system. A policy that admits the system actor is therefore only ever for finvest_worker.
    async with migrator_database.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT tablename, policyname, roles FROM pg_policies "
                "WHERE schemaname = 'public' "
                "AND (coalesce(qual, '') || coalesce(with_check, '')) LIKE '%''system''%'"
            )
        )
        offenders = [
            f"{row.tablename}.{row.policyname}"
            for row in rows
            if set(row.roles) != {"finvest_worker"}
        ]
    assert offenders == []
