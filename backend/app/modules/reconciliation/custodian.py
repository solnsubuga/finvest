"""Custodian statement checks. The rules are in `domain/custodian.py`; migration 0410 repeats
the ones about who may change what.

A check is uploaded by a member of staff, read by the worker, reviewed by one person and signed
off by another. The reader only copies the statement. The amounts are parsed and the holdings
compared here, against `dealing.holdings.holdings_on` for the statement's day, and compared
again after every correction and when the check is sent for sign-off, so the sign-off is always
of the comparison as it stands. Nothing here moves money.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.dealing import holdings as dealing_holdings
from app.modules.reconciliation.domain.custodian import (
    AmountScale,
    CheckStatus,
    CountedRow,
    CustodianCheckError,
    Held,
    Item,
    ItemKind,
    ParsedRow,
    Reading,
    check_note,
    compare,
    may_sign_off,
    parse_day,
    parse_row,
    unexplained,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

READ_KIND = "reconciliation.read_custodian_statement"
UPLOAD_OPERATION = "reconciliation.upload_custodian_statement"
SUBMIT_OPERATION = "reconciliation.submit_custodian_check"
SIGN_OFF_OPERATION = "reconciliation.sign_off_custodian_check"
SEND_BACK_OPERATION = "reconciliation.send_back_custodian_check"
# A reading that cannot be had within this many tries is recorded as unreadable, so a check is
# never left waiting. Each try costs a model call, so the budget is small.
READ_ATTEMPTS = 4


@dataclass(frozen=True, slots=True)
class CheckView:
    id: str
    statement_date: date
    status: CheckStatus
    file_key: str
    file_sha256: str
    file_content_type: str
    file_bytes: int
    uploaded_by: str
    uploaded_at: datetime
    reader: str | None
    read_at: datetime | None
    read_statement_date: date | None
    custodian_name: str | None
    account_reference: str | None
    amount_scale: AmountScale | None
    failure_code: str | None
    submitted_by: str | None
    submitted_at: datetime | None
    submit_note: str | None
    signed_off_by: str | None
    signed_off_at: datetime | None
    sign_off_note: str | None
    sent_back_by: str | None
    sent_back_note: str | None


@dataclass(frozen=True, slots=True)
class RowView:
    id: str
    line_no: int
    instrument: str
    code: str | None
    face_value_text: str
    currency_text: str | None
    matures_on_text: str | None
    location: str
    source_text: str
    currency: Currency | None
    face_minor: int | None
    matures_on: date | None
    problem: str | None
    corrected_face_minor: int | None
    corrected_matures_on: date | None
    left_out: bool
    corrected_by: str | None
    correction_reason: str | None

    def counted(self) -> CountedRow:
        """The row as the comparison counts it: a correction wins over what was read."""
        if self.left_out:
            return CountedRow(self.id, None, left_out=True)
        face = (
            self.corrected_face_minor if self.corrected_face_minor is not None else self.face_minor
        )
        day = self.corrected_matures_on or self.matures_on
        if face is None or day is None:
            return CountedRow(self.id, None, problem=self.problem)
        currency = self.currency or Currency.UGX
        return CountedRow(self.id, ParsedRow(face=Money(face, currency), matures_on=day))


@dataclass(frozen=True, slots=True)
class ItemView:
    item: Item
    note: str | None
    noted_by: str | None


@dataclass(frozen=True, slots=True)
class PendingRead:
    check_id: str
    file_key: str
    file_sha256: str
    file_content_type: str


_CHECK_COLUMNS = (
    "id, statement_date, status, file_key, file_sha256, file_content_type, file_bytes, "
    "uploaded_by, uploaded_at, reader, read_at, read_statement_date, custodian_name, "
    "account_reference, amount_scale, failure_code, submitted_by, submitted_at, submit_note, "
    "signed_off_by, signed_off_at, sign_off_note, sent_back_by, sent_back_note"
)
_ROW_COLUMNS = (
    "id, line_no, instrument, code, face_value_text, currency_text, matures_on_text, location, "
    "source_text, currency, face_minor, matures_on, problem, corrected_face_minor, "
    "corrected_matures_on, left_out, corrected_by, correction_reason"
)
_GET = text(f"SELECT {_CHECK_COLUMNS} FROM reconciliation_custodian_checks WHERE id = :id")  # noqa: S608
_LOCK = text(
    f"SELECT {_CHECK_COLUMNS} FROM reconciliation_custodian_checks WHERE id = :id "  # noqa: S608
    "FOR UPDATE"
)
_LIST = text(
    f"SELECT {_CHECK_COLUMNS} FROM reconciliation_custodian_checks "  # noqa: S608
    "ORDER BY uploaded_at DESC, id DESC LIMIT :limit"
)
_INSERT_CHECK = text(
    "INSERT INTO reconciliation_custodian_checks (id, statement_date, file_key, file_sha256, "
    "file_content_type, file_bytes, status, uploaded_by, uploaded_at, updated_at) VALUES "
    "(:id, :statement_date, :file_key, :file_sha256, :file_content_type, :file_bytes, "
    "'reading', :uploaded_by, :now, :now)"
)
_INSERT_ROW = text(
    "INSERT INTO reconciliation_custodian_rows (id, check_id, line_no, instrument, code, "
    "face_value_text, currency_text, matures_on_text, location, source_text, currency, "
    "face_minor, matures_on, problem) VALUES (:id, :check_id, :line_no, :instrument, :code, "
    ":face_value_text, :currency_text, :matures_on_text, :location, :source_text, :currency, "
    ":face_minor, :matures_on, :problem)"
)
_ROWS = text(
    f"SELECT {_ROW_COLUMNS} FROM reconciliation_custodian_rows "  # noqa: S608
    "WHERE check_id = :check_id ORDER BY line_no"
)
_ROW_FOR_UPDATE = text(
    f"SELECT {_ROW_COLUMNS} FROM reconciliation_custodian_rows "  # noqa: S608
    "WHERE check_id = :check_id AND id = :id FOR UPDATE"
)
_CORRECT_ROW = text(
    "UPDATE reconciliation_custodian_rows SET corrected_face_minor = :face, "
    "corrected_matures_on = :matures_on, left_out = :left_out, corrected_by = :by, "
    "corrected_at = :now, correction_reason = :reason WHERE id = :id"
)
_CLEAR_ITEMS = text("DELETE FROM reconciliation_custodian_items WHERE check_id = :check_id")
_INSERT_ITEM = text(
    "INSERT INTO reconciliation_custodian_items (check_id, item_key, position, kind, currency, "
    "matures_on, finvest_face_minor, custodian_face_minor, product_codes, row_ids, problem) "
    "VALUES (:check_id, :item_key, :position, :kind, :currency, :matures_on, :finvest, "
    ":custodian, :product_codes, :row_ids, :problem)"
)
_ITEMS = text(
    "SELECT i.item_key, i.kind, i.currency, i.matures_on, i.finvest_face_minor, "
    "i.custodian_face_minor, i.product_codes, i.row_ids, i.problem, n.note, n.noted_by "
    "FROM reconciliation_custodian_items AS i LEFT JOIN reconciliation_custodian_notes AS n "
    "ON n.check_id = i.check_id AND n.item_key = i.item_key "
    "WHERE i.check_id = :check_id ORDER BY i.position"
)
_NOTES = text(
    "SELECT item_key, note FROM reconciliation_custodian_notes WHERE check_id = :check_id"
)
_UPSERT_NOTE = text(
    "INSERT INTO reconciliation_custodian_notes (check_id, item_key, note, noted_by, noted_at) "
    "VALUES (:check_id, :item_key, :note, :by, :now) ON CONFLICT (check_id, item_key) "
    "DO UPDATE SET note = excluded.note, noted_by = excluded.noted_by, "
    "noted_at = excluded.noted_at"
)
_ITEM_EXISTS = text(
    "SELECT 1 FROM reconciliation_custodian_items WHERE check_id = :check_id "
    "AND item_key = :item_key"
)
_RECORD_READING = text(
    "UPDATE reconciliation_custodian_checks SET status = 'to_review', reader = :reader, "
    "read_at = :now, read_statement_date = :read_date, custodian_name = :custodian_name, "
    "account_reference = :account_reference, amount_scale = :scale, updated_at = :now "
    "WHERE id = :id AND status = 'reading'"
)
_RECORD_UNREADABLE = text(
    "UPDATE reconciliation_custodian_checks SET status = 'unreadable', reader = :reader, "
    "read_at = :now, failure_code = :code, updated_at = :now "
    "WHERE id = :id AND status = 'reading'"
)
_TOUCH = text("UPDATE reconciliation_custodian_checks SET updated_at = :now WHERE id = :id")
_SUBMIT = text(
    "UPDATE reconciliation_custodian_checks SET status = 'waiting_sign_off', submitted_by = :by, "
    "submitted_at = :now, submit_note = :note, updated_at = :now WHERE id = :id"
)
_SIGN_OFF = text(
    "UPDATE reconciliation_custodian_checks SET status = 'signed_off', signed_off_by = :by, "
    "signed_off_at = :now, sign_off_note = :note, updated_at = :now WHERE id = :id"
)
_SEND_BACK = text(
    "UPDATE reconciliation_custodian_checks SET status = 'to_review', submitted_by = NULL, "
    "submitted_at = NULL, submit_note = NULL, sent_back_by = :by, sent_back_at = :now, "
    "sent_back_note = :note, updated_at = :now WHERE id = :id"
)
_LAST_SIGNED = text(
    "SELECT id FROM reconciliation_custodian_checks WHERE status = 'signed_off' "
    "ORDER BY statement_date DESC, signed_off_at DESC LIMIT 1"
)


def file_key(check_id: str, file_id: str) -> str:
    return f"custodian/{check_id}/{file_id}"


# Upload -----------------------------------------------------------------------------------


async def upload(
    session: AsyncSession,
    staff: Actor,
    *,
    check_id: str,
    statement_date: date,
    stored_key: str,
    sha256: str,
    content_type: str,
    byte_size: int,
    idempotency_key: str,
    clock: Clock,
) -> CheckView:
    """Record an uploaded statement and ask the worker to read it.

    A retry with the same `idempotency_key` and the same file returns the first check; the
    caller then removes the file it stored the second time (`check.file_key` differs)."""
    _staff_only(staff)
    if statement_date > business_date(clock.now()):
        raise CustodianCheckError("statement_date_in_the_future")

    async def command() -> Receipt:
        now = clock.now()
        await session.execute(
            _INSERT_CHECK,
            {
                "id": check_id,
                "statement_date": statement_date,
                "file_key": stored_key,
                "file_sha256": sha256,
                "file_content_type": content_type,
                "file_bytes": byte_size,
                "uploaded_by": staff.id,
                "now": now,
            },
        )
        await enqueue(
            session,
            JobRequest(
                kind=READ_KIND,
                idempotency_key=f"custodian-read:{check_id}",
                payload={"check_id": check_id},
                max_attempts=READ_ATTEMPTS,
            ),
            now=now,
        )
        await _audit(session, staff, "reconciliation.custodian_uploaded", check_id, clock)
        return Receipt(201, {"check_id": check_id})

    done = await run_once(
        session,
        actor=staff,
        operation=UPLOAD_OPERATION,
        key=idempotency_key,
        request={"statement_date": statement_date.isoformat(), "sha256": sha256},
        now=clock.now(),
        command=command,
    )
    return await get_check(session, str(done.receipt.response["check_id"]))


# Reading, in the worker -------------------------------------------------------------------


async def pending_read(session: AsyncSession, check_id: str) -> PendingRead | None:
    """The file to read, while the check still waits for its reading."""
    row = (await session.execute(_GET, {"id": check_id})).one_or_none()
    if row is None or row.status != CheckStatus.READING:
        return None
    return PendingRead(row.id, row.file_key, row.file_sha256, row.file_content_type)


async def record_reading(
    session: AsyncSession, check_id: str, reading: Reading, clock: Clock
) -> bool:
    """Save what the reader copied, what Finvest's code made of it, and the comparison.
    False when the check had already moved on."""
    check = (await session.execute(_LOCK, {"id": check_id})).one_or_none()
    if check is None or check.status != CheckStatus.READING:
        return False
    for line_no, read in enumerate(reading.rows, start=1):
        try:
            parsed: ParsedRow | None = parse_row(read, reading.amount_scale)
            problem = None
        except CustodianCheckError as error:
            parsed, problem = None, error.code
        await session.execute(
            _INSERT_ROW,
            {
                "id": push_id(),
                "check_id": check_id,
                "line_no": line_no,
                "instrument": read.instrument or "not given",
                "code": read.code,
                "face_value_text": read.face_value or "",
                "currency_text": read.currency,
                "matures_on_text": read.matures_on,
                "location": read.location,
                "source_text": read.source_text or "",
                "currency": parsed.face.currency.value if parsed else None,
                "face_minor": parsed.face.minor if parsed else None,
                "matures_on": parsed.matures_on if parsed else None,
                "problem": problem,
            },
        )
    await _compare(session, check_id, check.statement_date, parse_day(reading.statement_date))
    result = await session.execute(
        _RECORD_READING,
        {
            "id": check_id,
            "reader": reading.reader,
            "now": clock.now(),
            "read_date": parse_day(reading.statement_date),
            "custodian_name": reading.custodian_name,
            "account_reference": reading.account_reference,
            "scale": reading.amount_scale.value,
        },
    )
    return bool(getattr(result, "rowcount", 0))


async def record_unreadable(
    session: AsyncSession, check_id: str, code: str, reader: str | None, clock: Clock
) -> bool:
    result = await session.execute(
        _RECORD_UNREADABLE,
        {"id": check_id, "code": code[:40], "reader": reader, "now": clock.now()},
    )
    return bool(getattr(result, "rowcount", 0))


# Review -----------------------------------------------------------------------------------


async def correct_row(
    session: AsyncSession,
    staff: Actor,
    check_id: str,
    row_id: str,
    *,
    face_minor: int | None,
    matures_on: date | None,
    left_out: bool,
    reason: str,
    clock: Clock,
) -> CheckView:
    """Put right a row the reader got wrong, or leave it out, with a reason. The copied text is
    kept beside the correction, and the comparison is worked out again."""
    _staff_only(staff)
    check = await _locked(session, check_id)
    if check.status is not CheckStatus.TO_REVIEW:
        raise CustodianCheckError("check_not_in_review")
    row = (
        await session.execute(_ROW_FOR_UPDATE, {"check_id": check_id, "id": row_id})
    ).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    if face_minor is not None and face_minor <= 0:
        raise CustodianCheckError("row_amount_not_positive")
    if not left_out and face_minor is None and matures_on is None:
        raise CustodianCheckError("correction_empty")
    cleaned = check_note(reason)
    await session.execute(
        _CORRECT_ROW,
        {
            "id": row_id,
            "face": None if left_out else face_minor,
            "matures_on": None if left_out else matures_on,
            "left_out": left_out,
            "by": staff.id,
            "now": clock.now(),
            "reason": cleaned[:500],
        },
    )
    await _compare(session, check_id, check.statement_date, check.read_statement_date)
    await session.execute(_TOUCH, {"id": check_id, "now": clock.now()})
    await _audit(session, staff, "reconciliation.custodian_row_corrected", check_id, clock, cleaned)
    return await get_check(session, check_id)


async def note_item(
    session: AsyncSession, staff: Actor, check_id: str, item_key: str, note: str, clock: Clock
) -> None:
    """Explain one difference. Setting it again replaces the note."""
    _staff_only(staff)
    check = await _locked(session, check_id)
    if check.status is not CheckStatus.TO_REVIEW:
        raise CustodianCheckError("check_not_in_review")
    cleaned = check_note(note)
    found = await session.execute(_ITEM_EXISTS, {"check_id": check_id, "item_key": item_key})
    if found.one_or_none() is None:
        raise ApplicationError("not_found")
    await session.execute(
        _UPSERT_NOTE,
        {
            "check_id": check_id,
            "item_key": item_key,
            "note": cleaned,
            "by": staff.id,
            "now": clock.now(),
        },
    )
    await session.execute(_TOUCH, {"id": check_id, "now": clock.now()})


async def submit(
    session: AsyncSession,
    staff: Actor,
    check_id: str,
    note: str | None,
    *,
    idempotency_key: str,
    clock: Clock,
) -> CheckView:
    """Send the check for sign-off. The comparison is worked out again first, against the
    holdings as they stand, and every difference must have a note."""
    _staff_only(staff)
    cleaned = check_note(note) if note is not None and note.strip() else None

    async def command() -> Receipt:
        check = await _locked(session, check_id)
        if check.status is not CheckStatus.TO_REVIEW:
            raise CustodianCheckError("check_not_in_review")
        items = await _compare(session, check_id, check.statement_date, check.read_statement_date)
        found = await session.execute(_NOTES, {"check_id": check_id})
        notes = {row.item_key: row.note for row in found.all()}
        if unexplained(items, notes):
            raise CustodianCheckError("differences_unexplained")
        await session.execute(
            _SUBMIT, {"id": check_id, "by": staff.id, "now": clock.now(), "note": cleaned}
        )
        await _audit(session, staff, "reconciliation.custodian_submitted", check_id, clock, cleaned)
        return Receipt(200, {"check_id": check_id})

    await run_once(
        session,
        actor=staff,
        operation=SUBMIT_OPERATION,
        key=idempotency_key,
        request={"check_id": check_id, "note": cleaned},
        now=clock.now(),
        command=command,
    )
    return await get_check(session, check_id)


async def sign_off(
    session: AsyncSession,
    staff: Actor,
    check_id: str,
    note: str | None,
    *,
    idempotency_key: str,
    clock: Clock,
) -> CheckView:
    """The second person signs the check off. Never whoever sent it for sign-off."""
    _staff_only(staff)
    cleaned = check_note(note) if note is not None and note.strip() else None

    async def command() -> Receipt:
        check = await _locked(session, check_id)
        if check.status is not CheckStatus.WAITING_SIGN_OFF:
            raise CustodianCheckError("check_not_waiting")
        may_sign_off(submitted_by=check.submitted_by, signer=str(staff.id))
        await session.execute(
            _SIGN_OFF, {"id": check_id, "by": staff.id, "now": clock.now(), "note": cleaned}
        )
        await _audit(
            session,
            staff,
            "reconciliation.custodian_signed_off",
            check_id,
            clock,
            cleaned or "Second signature on a custodian statement check",
            category="approval",
        )
        return Receipt(200, {"check_id": check_id})

    await run_once(
        session,
        actor=staff,
        operation=SIGN_OFF_OPERATION,
        key=idempotency_key,
        request={"check_id": check_id, "note": cleaned},
        now=clock.now(),
        command=command,
    )
    return await get_check(session, check_id)


async def send_back(
    session: AsyncSession,
    staff: Actor,
    check_id: str,
    reason: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> CheckView:
    """The second person sends it back for more review, with a reason."""
    _staff_only(staff)
    cleaned = check_note(reason)

    async def command() -> Receipt:
        check = await _locked(session, check_id)
        if check.status is not CheckStatus.WAITING_SIGN_OFF:
            raise CustodianCheckError("check_not_waiting")
        may_sign_off(submitted_by=check.submitted_by, signer=str(staff.id))
        await session.execute(
            _SEND_BACK, {"id": check_id, "by": staff.id, "now": clock.now(), "note": cleaned}
        )
        await _audit(
            session,
            staff,
            "reconciliation.custodian_sent_back",
            check_id,
            clock,
            cleaned,
            category="approval",
        )
        return Receipt(200, {"check_id": check_id})

    await run_once(
        session,
        actor=staff,
        operation=SEND_BACK_OPERATION,
        key=idempotency_key,
        request={"check_id": check_id, "reason": cleaned},
        now=clock.now(),
        command=command,
    )
    return await get_check(session, check_id)


# Reading the record -----------------------------------------------------------------------


async def get_check(session: AsyncSession, check_id: str) -> CheckView:
    row = (await session.execute(_GET, {"id": check_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _check(row)


async def list_checks(session: AsyncSession, *, limit: int) -> list[CheckView]:
    rows = await session.execute(_LIST, {"limit": max(1, min(limit, 100))})
    return [_check(row) for row in rows.all()]


async def rows_of(session: AsyncSession, check_id: str) -> list[RowView]:
    return [_row(row) for row in (await session.execute(_ROWS, {"check_id": check_id})).all()]


async def items_of(session: AsyncSession, check_id: str) -> list[ItemView]:
    found = await session.execute(_ITEMS, {"check_id": check_id})
    return [
        ItemView(
            item=Item(
                key=row.item_key,
                kind=ItemKind(row.kind),
                currency=Currency(row.currency) if row.currency else None,
                matures_on=row.matures_on,
                finvest_face_minor=row.finvest_face_minor,
                custodian_face_minor=row.custodian_face_minor,
                product_codes=tuple(row.product_codes),
                row_ids=tuple(row.row_ids),
                problem=row.problem,
            ),
            note=row.note,
            noted_by=row.noted_by,
        )
        for row in found.all()
    ]


async def last_signed_off(session: AsyncSession) -> CheckView | None:
    """The latest statement day that has been signed off, for the dealing desk's summary."""
    row = (await session.execute(_LAST_SIGNED)).one_or_none()
    return None if row is None else await get_check(session, row.id)


# Internals --------------------------------------------------------------------------------


async def _compare(
    session: AsyncSession, check_id: str, statement_date: date, read_date: date | None
) -> tuple[Item, ...]:
    held = [
        Held(
            product_code=holding.product_code,
            currency=holding.currency,
            matures_on=holding.matures_on,
            face_minor=holding.face_minor,
        )
        for holding in await dealing_holdings.holdings_on(session, statement_date)
    ]
    rows = [row.counted() for row in await rows_of(session, check_id)]
    items = compare(held, rows, statement_date=statement_date, read_date=read_date)
    await session.execute(_CLEAR_ITEMS, {"check_id": check_id})
    for position, item in enumerate(items):
        await session.execute(
            _INSERT_ITEM,
            {
                "check_id": check_id,
                "item_key": item.key,
                "position": position,
                "kind": item.kind.value,
                "currency": item.currency.value if item.currency else None,
                "matures_on": item.matures_on,
                "finvest": item.finvest_face_minor,
                "custodian": item.custodian_face_minor,
                "product_codes": list(item.product_codes),
                "row_ids": list(item.row_ids),
                "problem": item.problem,
            },
        )
    return items


async def _locked(session: AsyncSession, check_id: str) -> CheckView:
    row = (await session.execute(_LOCK, {"id": check_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _check(row)


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


async def _audit(
    session: AsyncSession,
    staff: Actor,
    action: str,
    check_id: str,
    clock: Clock,
    reason: str | None = None,
    *,
    category: str = "change",
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if category == "approval" else "change",
            action=action,
            resource_type="reconciliation_custodian_check",
            resource_id=check_id,
            actor=AuditActor(staff.kind, staff.id),
            reason=reason,
        ),
        clock,
    )


def _check(row: Row[Any]) -> CheckView:
    return CheckView(
        id=row.id,
        statement_date=row.statement_date,
        status=CheckStatus(row.status),
        file_key=row.file_key,
        file_sha256=row.file_sha256,
        file_content_type=row.file_content_type,
        file_bytes=row.file_bytes,
        uploaded_by=row.uploaded_by,
        uploaded_at=row.uploaded_at,
        reader=row.reader,
        read_at=row.read_at,
        read_statement_date=row.read_statement_date,
        custodian_name=row.custodian_name,
        account_reference=row.account_reference,
        amount_scale=AmountScale(row.amount_scale) if row.amount_scale else None,
        failure_code=row.failure_code,
        submitted_by=row.submitted_by,
        submitted_at=row.submitted_at,
        submit_note=row.submit_note,
        signed_off_by=row.signed_off_by,
        signed_off_at=row.signed_off_at,
        sign_off_note=row.sign_off_note,
        sent_back_by=row.sent_back_by,
        sent_back_note=row.sent_back_note,
    )


def _row(row: Row[Any]) -> RowView:
    return RowView(
        id=row.id,
        line_no=row.line_no,
        instrument=row.instrument,
        code=row.code,
        face_value_text=row.face_value_text,
        currency_text=row.currency_text,
        matures_on_text=row.matures_on_text,
        location=row.location,
        source_text=row.source_text,
        currency=Currency(row.currency) if row.currency else None,
        face_minor=row.face_minor,
        matures_on=row.matures_on,
        problem=row.problem,
        corrected_face_minor=row.corrected_face_minor,
        corrected_matures_on=row.corrected_matures_on,
        left_out=row.left_out,
        corrected_by=row.corrected_by,
        correction_reason=row.correction_reason,
    )


def counted_rows(rows: Sequence[RowView]) -> list[CountedRow]:
    return [row.counted() for row in rows]
