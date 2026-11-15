"""Africa's Talking's delivery reports: checked and saved once by the API, applied by the worker."""

import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import RetryPolicy, Worker, WorkerConfig
from app.infrastructure.sms import SmsMessage, SmsResult
from app.modules.notifications import jobs, service, sms_delivery
from app.modules.notifications.service import queue_message
from app.modules.notifications.settings import SmsCallbackSettings
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.email import RecordingTransport

pytestmark = pytest.mark.integration

# Made for this run. The real token comes only from the environment.
TOKEN = secrets.token_urlsafe(32)
URL = f"/provider-callbacks/sms/africastalking/{TOKEN}"
FORM = {"content-type": "application/x-www-form-urlencoded"}


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"sms_reports_{secrets.token_hex(8)}"
    monkeypatch.setattr(service, "MESSAGE_QUEUE", name)
    return name


class AfricasTalkingRecording:
    """Sends as Africa's Talking does, with an id of its shape, so a report can find the text."""

    name = "africastalking"

    def __init__(self) -> None:
        self.ids: list[str] = []

    async def send(self, message: SmsMessage) -> SmsResult:
        self.ids.append(f"ATXid_{secrets.token_hex(16)}")
        return SmsResult(provider=self.name, provider_message_id=self.ids[-1])


def a_worker(database: Database, clock: FakeClock, sms: AfricasTalkingRecording) -> Worker:
    handlers = {
        **jobs.handlers(database, clock, RecordingTransport(name="resend"), sms),
        **sms_delivery.SmsReportJobs(database, clock).handlers(),
    }
    config = WorkerConfig(
        queue=service.MESSAGE_QUEUE,
        worker_id="sms-reports-test",
        retry=RetryPolicy(base=timedelta(seconds=10), cap=timedelta(minutes=1)),
    )
    return Worker(database, handlers, config, clock)


async def drain(worker: Worker) -> None:
    while await worker.run_once():
        pass


async def send_one(app_database: Database, worker: Worker, clock: FakeClock) -> str:
    key = f"test:{push_id()}"
    async with app_database.transaction() as session:
        await queue_message(
            session,
            channel="sms",
            recipient="+256772123456",
            template="sign_in_code",
            variables={"code": "482913", "minutes": 10},
            idempotency_key=key,
            now=clock.now(),
            deliver_within=timedelta(minutes=10),
        )
    await drain(worker)
    return key


def report(message_id: str, status: str, **extra: str) -> bytes:
    return urlencode(
        {"id": message_id, "status": status, "phoneNumber": "+256772123456", **extra}
    ).encode()


def api(
    test_database: TestDatabase, database: Database, clock: FakeClock, token: str | None = TOKEN
) -> httpx.AsyncClient:
    app = create_app(
        test_database.settings("finvest_app"),
        database,
        clock,
        sms_callbacks=SmsCallbackSettings(
            callback_token=SecretStr(token) if token is not None else None
        ),
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="https://api.finvestug.com")


async def scalar(database: Database, sql: str, **values: object) -> object:
    async with database.transaction() as session:
        return (await session.execute(text(sql), values)).scalar_one_or_none()


async def message_row(database: Database, key: str):  # type: ignore[no-untyped-def]
    async with database.transaction() as session:
        return (
            await session.execute(
                text(
                    "SELECT status, provider, delivery, delivery_at, delivery_reason "
                    "FROM notification_messages WHERE idempotency_key = :key"
                ),
                {"key": key},
            )
        ).one()


async def test_a_delivery_report_is_recorded_once_and_applied(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, sms = FakeClock(), AfricasTalkingRecording()
    worker = a_worker(worker_database, clock, sms)
    key = await send_one(app_database, worker, clock)
    [message_id] = sms.ids
    async with api(test_database, app_database, clock) as http:
        content = report(message_id, "Success", networkCode="64110", retryCount="0")
        assert (await http.post(URL, content=content, headers=FORM)).status_code == 204
        # Africa's Talking sends a report again when it thinks ours was lost.
        assert (await http.post(URL, content=content, headers=FORM)).status_code == 204
    assert (
        await scalar(
            worker_database,
            # This message's reports only: other tests queue reports of their own.
            "SELECT count(*) FROM jobs WHERE kind = :kind AND payload ->> 'message_id' = :id",
            kind=sms_delivery.APPLY_REPORT,
            id=message_id,
        )
        == 1
    )
    await drain(worker)
    row = await message_row(worker_database, key)
    assert (row.status, row.provider, row.delivery, row.delivery_reason) == (
        "sent",
        "africastalking",
        "delivered",
        None,
    )
    assert row.delivery_at == clock.now()
    stored = await scalar(
        worker_database,
        "SELECT outcome FROM notification_sms_reports WHERE provider_message_id = :id",
        id=message_id,
    )
    assert stored == "applied"


async def test_the_phone_number_in_a_report_is_never_stored(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock = FakeClock()
    message_id = f"ATXid_{secrets.token_hex(16)}"
    async with api(test_database, app_database, clock) as http:
        await http.post(URL, content=report(message_id, "Success"), headers=FORM)
    stored = await scalar(
        worker_database,
        "SELECT row_to_json(r)::text FROM notification_sms_reports r "
        "WHERE provider_message_id = :id",
        id=message_id,
    )
    assert isinstance(stored, str) and "256772123456" not in stored


async def test_a_failure_keeps_its_reason_and_a_later_delivery_clears_it(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, sms = FakeClock(), AfricasTalkingRecording()
    worker = a_worker(worker_database, clock, sms)
    key = await send_one(app_database, worker, clock)
    [message_id] = sms.ids
    async with api(test_database, app_database, clock) as http:
        failed = report(message_id, "Failed", failureReason="AbsentSubscriber", retryCount="1")
        await http.post(URL, content=failed, headers=FORM)
        await drain(worker)
        row = await message_row(worker_database, key)
        assert (row.delivery, row.delivery_reason) == ("failed", "AbsentSubscriber")
        # Africa's Talking tried again, and the phone took it.
        clock.advance(minutes=3)
        await http.post(URL, content=report(message_id, "Success"), headers=FORM)
        await drain(worker)
        row = await message_row(worker_database, key)
        assert (row.delivery, row.delivery_reason) == ("delivered", None)
        # A report that arrives late changes nothing.
        await http.post(URL, content=report(message_id, "Buffered"), headers=FORM)
        await drain(worker)
    row = await message_row(worker_database, key)
    assert row.delivery == "delivered"
    late = await scalar(
        worker_database,
        "SELECT outcome FROM notification_sms_reports WHERE provider_message_id = :id "
        "AND status = 'Buffered'",
        id=message_id,
    )
    assert late == "unchanged"


async def test_a_report_for_no_text_of_ours_waits_then_is_kept_as_unmatched(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, sms = FakeClock(), AfricasTalkingRecording()
    worker = a_worker(worker_database, clock, sms)
    message_id = f"ATXid_{secrets.token_hex(16)}"
    async with api(test_database, app_database, clock) as http:
        await http.post(URL, content=report(message_id, "Success"), headers=FORM)
    await drain(worker)
    query = "SELECT outcome FROM notification_sms_reports WHERE provider_message_id = :id"
    assert await scalar(worker_database, query, id=message_id) is None
    clock.advance(minutes=11)
    await drain(worker)
    assert await scalar(worker_database, query, id=message_id) == "unmatched"


async def test_a_report_for_an_email_or_another_provider_changes_nothing(
    test_database: TestDatabase, app_database: Database, worker_database: Database
) -> None:
    clock, sms = FakeClock(), AfricasTalkingRecording()
    worker = a_worker(worker_database, clock, sms)
    key = await send_one(app_database, worker, clock)
    [message_id] = sms.ids
    async with worker_database.transaction() as session:
        await session.execute(
            text("UPDATE notification_messages SET provider = 'iotec' WHERE idempotency_key = :k"),
            {"k": key},
        )
    async with api(test_database, app_database, clock) as http:
        await http.post(URL, content=report(message_id, "Failed"), headers=FORM)
    clock.advance(minutes=11)
    await drain(worker)
    assert (await message_row(worker_database, key)).delivery is None


@pytest.mark.parametrize("token", [None, TOKEN], ids=["unset", "wrong"])
async def test_a_wrong_or_unset_token_is_not_found(
    test_database: TestDatabase,
    app_database: Database,
    worker_database: Database,
    token: str | None,
) -> None:
    clock = FakeClock()
    message_id = f"ATXid_{secrets.token_hex(16)}"
    wrong = f"/provider-callbacks/sms/africastalking/{secrets.token_urlsafe(32)}"
    async with api(test_database, app_database, clock, token=token) as http:
        response = await http.post(
            wrong if token else URL, content=report(message_id, "Success"), headers=FORM
        )
    assert response.status_code == 404
    assert (
        await scalar(
            worker_database,
            "SELECT count(*) FROM notification_sms_reports WHERE provider_message_id = :id",
            id=message_id,
        )
        == 0
    )


async def test_an_unreadable_or_oversized_report_is_refused(
    test_database: TestDatabase, app_database: Database
) -> None:
    clock = FakeClock()
    async with api(test_database, app_database, clock) as http:
        unreadable = await http.post(URL, content=b"status=Success", headers=FORM)
        oversized = await http.post(URL, content=b"x" * 5000, headers=FORM)
        unknown = await http.post(URL, content=report("ATXid_1", "SomethingNew"), headers=FORM)
    assert (unreadable.status_code, oversized.status_code, unknown.status_code) == (
        422,
        413,
        204,
    )
