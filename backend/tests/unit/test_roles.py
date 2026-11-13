from dataclasses import replace

from app.infrastructure.database.roles import APP, MIGRATOR, WORKER, RoleFacts, problems

APP_FACTS = RoleFacts(
    current_user=APP,
    superuser=False,
    bypass_rls=False,
    create_role=False,
    create_database=False,
    create_in_public=False,
    owns_tables=False,
)


def test_a_correct_application_role_has_no_problems() -> None:
    assert problems(APP, APP_FACTS) == []
    assert problems(WORKER, replace(APP_FACTS, current_user=WORKER)) == []


def test_the_migrator_must_be_able_to_create() -> None:
    facts = replace(APP_FACTS, current_user=MIGRATOR, create_in_public=True, owns_tables=True)
    assert problems(MIGRATOR, facts) == []
    assert problems(MIGRATOR, replace(facts, create_in_public=False))


def test_every_escape_from_the_boundary_is_named() -> None:
    assert "expected" in problems(APP, replace(APP_FACTS, current_user="postgres"))[0]
    assert "SUPERUSER" in problems(APP, replace(APP_FACTS, superuser=True))[0]
    assert "BYPASSRLS" in problems(APP, replace(APP_FACTS, bypass_rls=True))[0]
    assert "CREATEROLE" in problems(APP, replace(APP_FACTS, create_role=True))[0]
    assert "CREATEDB" in problems(APP, replace(APP_FACTS, create_database=True))[0]
    assert "create in schema" in problems(APP, replace(APP_FACTS, create_in_public=True))[0]
    assert "owns tables" in problems(APP, replace(APP_FACTS, owns_tables=True))[0]
