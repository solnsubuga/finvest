"""Checking the custodian's statement against the dealing desk's holdings, on real PostgreSQL.

The test database is shared, so other tests' bills are held too. Each test buys bills of a
maturity no other test uses and looks at that maturity's line, and explains every other
difference before sending a check for sign-off.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from itertools import count

import pytest
from httpx import Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.infrastructure.storage import MemoryStorage, NewFile
from app.modules.reconciliation import custodian
from app.modules.reconciliation.custodian_jobs import CustodianJobs
from app.modules.reconciliation.custodian_reading import DevelopmentStubReader
from app.modules.reconciliation.domain.custodian import (
    CheckStatus,
    CustodianCheckError,
    ItemKind,
    ReaderUnavailable,
    Reading,
    StatementFile,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.integration.test_dealing_settlement import TODAY, bought
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.jobs import run_due
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

_maturities = count()


def maturity() -> date:
    """A maturity date after every dealing test's, inside the 182 day term.

    The bills bought here are never paid or sold, so they must not mature before a date
    that a maturity run in another test pays up to. The module is named to run after the
    dealing tests, like the other modules that leave bills behind, so the allocation tests
    see only their own lots.
    """
    return TODAY + timedelta(days=170 + next(_maturities))


# The dealing tests settle bills on TODAY, so the statement is for that day, read the day after.
CLOCK_AT = datetime(2026, 11, 20, 9, tzinfo=UTC)
ROUTE = "/staff/reconciliation/custodian-checks"


def statement(*rows: tuple[str, str], day: date = TODAY) -> bytes:
    lines = ["instrument,code,face_value,currency,maturity_date,statement_date"]
    lines += [f'182 day bill,,"{face}",UGX,{matures},{day.isoformat()}' for face, matures in rows]
    return ("\n".join(lines) + "\n").encode()


async def uploaded(
    app: Database,
    storage: MemoryStorage,
    clock: FakeClock,
    data: bytes,
    staff: Actor,
    content_type: str = "text/csv",
) -> str:
    check_id = push_id()
    stored = await storage.put(NewFile(custodian.file_key(check_id, push_id()), data, content_type))
    async with app.actor_transaction(staff) as session:
        view = await custodian.upload(
            session,
            staff,
            check_id=check_id,
            statement_date=TODAY,
            stored_key=stored.key,
            sha256=stored.sha256,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            idempotency_key=push_id(),
            clock=clock,
        )
    assert view.status is CheckStatus.READING
    return check_id


async def read_all(
    worker: Database, storage: MemoryStorage, clock: FakeClock, reader: object
) -> None:
    jobs = CustodianJobs(worker, reader, storage, clock)  # type: ignore[arg-type]
    await run_due(Worker(worker, jobs.handlers(), WorkerConfig(), clock))


async def detail(
    app: Database, check_id: str
) -> tuple[custodian.CheckView, list[custodian.RowView], list[custodian.ItemView]]:
    async with app.actor_transaction(new_staff()) as session:
        return (
            await custodian.get_check(session, check_id),
            await custodian.rows_of(session, check_id),
            await custodian.items_of(session, check_id),
        )


def item(items: list[custodian.ItemView], matures_on: date) -> custodian.ItemView:
    return next(i for i in items if i.item.matures_on == matures_on and i.item.currency)


async def explain_everything(app: Database, staff: Actor, check_id: str, clock: FakeClock) -> None:
    _, _, items = await detail(app, check_id)
    async with app.actor_transaction(staff) as session:
        for found in items:
            if found.item.needs_note:
                await custodian.note_item(
                    session, staff, check_id, found.item.key, "Held by another test", clock
                )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(CLOCK_AT)


async def test_a_statement_is_read_compared_corrected_and_signed_off_by_two_people(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> None:
    storage = MemoryStorage()
    matches, differs = maturity(), maturity()
    await bought(app_database, 300_000_000, 290_000_000, matches)
    await bought(app_database, 200_000_000, 195_000_000, differs)
    reviewer, signer = new_staff(), new_staff()
    check_id = await uploaded(
        app_database,
        storage,
        clock,
        statement(
            ("300,000,000", matches.isoformat()),
            # Misread: the statement shows 200,000,000.
            ("20,000,000", differs.isoformat()),
            ("about 5m", "2027-09-01"),
        ),
        reviewer,
    )
    await read_all(worker_database, storage, clock, DevelopmentStubReader())

    check, rows, items = await detail(app_database, check_id)
    assert check.status is CheckStatus.TO_REVIEW
    assert check.reader == "development-stub"
    assert check.read_statement_date == TODAY
    assert [(r.face_value_text, r.face_minor, r.problem) for r in rows] == [
        ("300,000,000", 300_000_000, None),
        ("20,000,000", 20_000_000, None),
        ("about 5m", None, "row_amount_unreadable"),
    ]
    assert item(items, matches).item.kind is ItemKind.MATCH
    wrong = item(items, differs).item
    assert (wrong.kind, wrong.difference_minor) == (ItemKind.AMOUNT_DIFFERS, -180_000_000)
    assert any(i.item.kind is ItemKind.ROW_UNREADABLE for i in items)

    # Nothing can be sent while a difference has no note.
    async with app_database.actor_transaction(reviewer) as session:
        with pytest.raises(CustodianCheckError) as refused:
            await custodian.submit(
                session, reviewer, check_id, None, idempotency_key=push_id(), clock=clock
            )
    assert refused.value.code == "differences_unexplained"

    # The reviewer puts the misread right and leaves the unreadable line out.
    async with app_database.actor_transaction(reviewer) as session:
        await custodian.correct_row(
            session,
            reviewer,
            check_id,
            rows[1].id,
            face_minor=200_000_000,
            matures_on=None,
            left_out=False,
            reason="The statement shows 200,000,000",
            clock=clock,
        )
        await custodian.correct_row(
            session,
            reviewer,
            check_id,
            rows[2].id,
            face_minor=None,
            matures_on=None,
            left_out=True,
            reason="A cash line, not a bill",
            clock=clock,
        )
    _, rows, items = await detail(app_database, check_id)
    assert rows[1].face_value_text == "20,000,000"  # what was read is kept beside the fix
    assert rows[1].corrected_face_minor == 200_000_000
    assert item(items, differs).item.kind is ItemKind.MATCH
    assert not any(i.item.kind is ItemKind.ROW_UNREADABLE for i in items)

    await explain_everything(app_database, reviewer, check_id, clock)
    submit_key = push_id()
    async with app_database.actor_transaction(reviewer) as session:
        sent = await custodian.submit(
            session, reviewer, check_id, "Checked line by line", idempotency_key=submit_key,
            clock=clock,
        )  # fmt: skip
    assert sent.status is CheckStatus.WAITING_SIGN_OFF
    async with app_database.actor_transaction(reviewer) as session:
        again = await custodian.submit(
            session, reviewer, check_id, "Checked line by line", idempotency_key=submit_key,
            clock=clock,
        )  # fmt: skip
    assert again.status is CheckStatus.WAITING_SIGN_OFF

    # The reviewer cannot sign their own check off.
    async with app_database.actor_transaction(reviewer) as session:
        with pytest.raises(CustodianCheckError) as own:
            await custodian.sign_off(
                session, reviewer, check_id, None, idempotency_key=push_id(), clock=clock
            )
    assert own.value.code == "check_needs_second_person"

    # Sent back, put right, sent again, signed off.
    async with app_database.actor_transaction(signer) as session:
        back = await custodian.send_back(
            session, signer, check_id, "Say which cash line", idempotency_key=push_id(),
            clock=clock,
        )  # fmt: skip
    assert (back.status, back.sent_back_note) == (CheckStatus.TO_REVIEW, "Say which cash line")
    async with app_database.actor_transaction(reviewer) as session:
        await custodian.submit(
            session, reviewer, check_id, None, idempotency_key=push_id(), clock=clock
        )
    async with app_database.actor_transaction(signer) as session:
        signed = await custodian.sign_off(
            session, signer, check_id, "Agreed", idempotency_key=push_id(), clock=clock
        )
    assert (signed.status, signed.signed_off_by) == (CheckStatus.SIGNED_OFF, signer.id)

    # A signed-off check never changes, whatever the role.
    async with app_database.actor_transaction(reviewer) as session:
        with pytest.raises(CustodianCheckError):
            await custodian.note_item(session, reviewer, check_id, items[0].item.key, "x", clock)
    with pytest.raises(DBAPIError, match="never changes"):
        async with app_database.actor_transaction(signer) as session:
            await session.execute(
                text(
                    "UPDATE reconciliation_custodian_checks SET sign_off_note = 'edited' "
                    "WHERE id = :id"
                ),
                {"id": check_id},
            )


async def test_the_database_refuses_one_person_signing_both_sides(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> None:
    storage = MemoryStorage()
    staff = new_staff()
    check_id = await uploaded(app_database, storage, clock, statement(), staff)
    await read_all(worker_database, storage, clock, DevelopmentStubReader())
    await explain_everything(app_database, staff, check_id, clock)
    async with app_database.actor_transaction(staff) as session:
        await custodian.submit(
            session, staff, check_id, None, idempotency_key=push_id(), clock=clock
        )
    with pytest.raises(DBAPIError, match="two_people"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "UPDATE reconciliation_custodian_checks SET status = 'signed_off', "
                    "signed_off_by = :me, signed_off_at = now() WHERE id = :id"
                ),
                {"id": check_id, "me": staff.id},
            )
    # Staff cannot write what the reader said, and the worker cannot review.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("UPDATE reconciliation_custodian_checks SET reader = 'me' WHERE id = :id"),
                {"id": check_id},
            )
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE reconciliation_custodian_checks SET status = 'to_review' WHERE id = :id"
                ),
                {"id": check_id},
            )


async def test_two_signers_at_once_sign_off_once(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> None:
    storage = MemoryStorage()
    reviewer = new_staff()
    check_id = await uploaded(app_database, storage, clock, statement(), reviewer)
    await read_all(worker_database, storage, clock, DevelopmentStubReader())
    await explain_everything(app_database, reviewer, check_id, clock)
    async with app_database.actor_transaction(reviewer) as session:
        await custodian.submit(
            session, reviewer, check_id, None, idempotency_key=push_id(), clock=clock
        )
    barrier = AsyncBarrier(2)

    async def sign(signer: Actor) -> str:
        await barrier.wait()
        try:
            async with app_database.actor_transaction(signer) as session:
                await custodian.sign_off(
                    session, signer, check_id, None, idempotency_key=push_id(), clock=clock
                )
        except CustodianCheckError as error:
            return error.code
        return "signed"

    outcomes = await asyncio.gather(sign(new_staff()), sign(new_staff()))
    assert sorted(outcomes) == ["check_not_waiting", "signed"]


async def test_an_unreadable_statement_is_recorded_and_never_waits(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> None:
    storage = MemoryStorage()
    staff = new_staff()
    pdf = await uploaded(
        app_database, storage, clock, b"%PDF-1.7 not for the stub", staff, "application/pdf"
    )
    no_reader = await uploaded(app_database, storage, clock, statement(), staff)
    await read_all(worker_database, storage, clock, DevelopmentStubReader())
    check, rows, items = await detail(app_database, pdf)
    assert (check.status, check.failure_code) == (CheckStatus.UNREADABLE, "stub_reads_csv_only")
    assert rows == [] and items == []
    assert (await detail(app_database, no_reader))[0].status is CheckStatus.TO_REVIEW

    unread = await uploaded(app_database, storage, clock, statement(), staff)
    await read_all(worker_database, storage, clock, None)
    assert (await detail(app_database, unread))[0].failure_code == "reader_off"


class FlakyReader:
    """Never answers, so the last try records the check as unreadable."""

    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def read(self, file: StatementFile, *, key: str) -> Reading:
        self.calls += 1
        raise ReaderUnavailable()


async def test_a_reader_that_never_answers_is_given_up_on(
    app_database: Database, worker_database: Database, clock: FakeClock
) -> None:
    storage = MemoryStorage()
    check_id = await uploaded(app_database, storage, clock, statement(), new_staff())
    flaky = FlakyReader()
    for _ in range(custodian.READ_ATTEMPTS):
        await read_all(worker_database, storage, clock, flaky)
        clock.advance(hours=1)
    check, _, _ = await detail(app_database, check_id)
    assert (check.status, check.failure_code) == (CheckStatus.UNREADABLE, "reader_unavailable")
    assert flaky.calls == custodian.READ_ATTEMPTS


# Routes ------------------------------------------------------------------------------------


def code(response: Response) -> str:
    error = response.json()["error"]
    fields = error.get("fields") or []
    return str(fields[0]["code"] if fields else error["code"])


async def test_staff_upload_review_and_sign_off_through_the_routes(
    test_database: TestDatabase,
    app_database: Database,
    worker_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = MemoryStorage()
    monkeypatch.setenv("FINVEST_CUSTODIAN_READER", "development-stub")
    async with running_api(test_database, app_database, storage=storage) as api:
        api.clock.current = CLOCK_AT
        await api_flow(api, worker_database, storage)


async def api_flow(api: Api, worker_database: Database, storage: MemoryStorage) -> None:
    matures = maturity()
    await bought(api.database, 100_000_000, 97_000_000, matures)
    await api.staff("finance", name="Daniel Opio")
    listed = await api.http.get(ROUTE)
    assert listed.status_code == 200, listed.text
    assert listed.json()["reading_on"] is True

    future = await api.http.post(
        ROUTE,
        data={"statement_date": (TODAY + timedelta(days=5)).isoformat()},
        files={"file": ("statement.csv", statement(), "text/csv")},
        headers={"Idempotency-Key": push_id()},
    )
    assert (future.status_code, code(future)) == (422, "statement_date_in_the_future")
    binary = await api.http.post(
        ROUTE,
        data={"statement_date": TODAY.isoformat()},
        files={"file": ("x.exe", b"MZ\x00\x00\x00", "application/octet-stream")},
        headers={"Idempotency-Key": push_id()},
    )
    assert (binary.status_code, code(binary)) == (422, "statement_type_not_supported")

    key = push_id()
    body = statement(("100,000,000", matures.isoformat()))
    sent = await api.http.post(
        ROUTE,
        data={"statement_date": TODAY.isoformat()},
        # A browser may call a CSV anything; the bytes decide.
        files={"file": ("statement.csv", body, "application/vnd.ms-excel")},
        headers={"Idempotency-Key": key},
    )
    assert sent.status_code == 201, sent.text
    check = sent.json()
    assert (check["status"], check["file_content_type"]) == ("reading", "text/csv")
    again = await api.http.post(
        ROUTE,
        data={"statement_date": TODAY.isoformat()},
        files={"file": ("statement.csv", body, "text/csv")},
        headers={"Idempotency-Key": key},
    )
    assert again.json()["id"] == check["id"]
    # The retry's second copy was removed; only the recorded file is kept.
    assert [k for k in storage.files if k.startswith(f"custodian/{check['id']}/")] != []
    assert sum(1 for k in storage.files if k.startswith("custodian/")) == 1

    await read_all(worker_database, storage, api.clock, DevelopmentStubReader())
    one = await api.http.get(f"{ROUTE}/{check['id']}")
    assert one.status_code == 200, one.text
    found = one.json()
    assert found["check"]["status"] == "to_review"
    assert found["check"]["allowed"] == ["correct", "note", "submit"]
    mine = next(i for i in found["items"] if i["matures_on"] == matures.isoformat())
    assert mine["kind"] == "match"
    assert found["rows"][0]["source_text"].startswith("182 day bill")

    link = await api.http.get(f"{ROUTE}/{check['id']}/file")
    assert link.status_code == 200
    assert link.json()["content_type"] == "text/csv"

    early = await api.http.post(
        f"{ROUTE}/{check['id']}/submit", json={}, headers={"Idempotency-Key": push_id()}
    )
    if found["unexplained"]:
        assert (early.status_code, code(early)) == (409, "differences_unexplained")
        for each in found["items"]:
            if each["needs_note"]:
                noted = await api.http.put(
                    f"{ROUTE}/{check['id']}/notes/{each['key']}",
                    json={"note": "Held by another test"},
                )
                assert noted.status_code == 200, noted.text
        early = await api.http.post(
            f"{ROUTE}/{check['id']}/submit", json={}, headers={"Idempotency-Key": push_id()}
        )
    assert early.status_code == 200, early.text
    assert early.json()["check"]["allowed"] == []

    own = await api.http.post(
        f"{ROUTE}/{check['id']}/sign-off", json={}, headers={"Idempotency-Key": push_id()}
    )
    assert (own.status_code, code(own)) == (403, "check_needs_second_person")

    await api.staff("admin", name="Grace Auma")
    waiting = await api.http.get(f"{ROUTE}/{check['id']}")
    assert waiting.json()["check"]["allowed"] == ["sign_off", "send_back"]
    signed = await api.http.post(
        f"{ROUTE}/{check['id']}/sign-off",
        json={"note": "Agreed with the statement"},
        headers={"Idempotency-Key": push_id()},
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["check"]["status"] == "signed_off"

    await api.staff("support")
    refused = await api.http.get(ROUTE)
    assert refused.status_code == 403
