"""Product types and the changes two members of staff make to them.

Every function works inside the caller's transaction, opened for the same actor passed here.
The rules are in `domain/types.py`. Migration 0493 holds the important ones in the database
too: a type is written only by an approval, with exactly the values approved, by the person the
transaction acts for, and a product or bond issue takes only a type in use of its own model.

Other modules call `type_for` to check the type a new product or issue names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.product_types.domain.types import (
    BUILT_IN,
    ChangeStatus,
    NewType,
    Refusal,
    ReturnModel,
    TypeAction,
    TypeStatus,
    TypeTerms,
    check_decision,
    check_edit,
    check_new,
    check_status,
    check_withdrawal,
    status_after,
    valid_text,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

CHANGE_RESOURCE = "product_type_change"
MAX_CHANGES = 200


@dataclass(frozen=True, slots=True)
class TypeView:
    id: str
    code: str
    model: ReturnModel
    terms: TypeTerms
    status: TypeStatus
    built_in: bool
    version: int
    # Products and bond issues of this type, of any status.
    in_use: int

    @property
    def name(self) -> str:
        return self.terms.name


@dataclass(frozen=True, slots=True)
class ChangeView:
    id: str
    action: TypeAction
    # The type's code: the new one for a new type.
    code: str
    model: ReturnModel
    # What the change sets. None for retiring or restoring.
    terms: TypeTerms | None
    base_version: int | None
    reason: str
    status: ChangeStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


_TYPE_COLUMNS = (
    "t.id, t.code, t.name, t.model, t.description, t.default_tax_bps, t.status, t.built_in, "
    "t.version, (SELECT count(*) FROM investment_products p WHERE p.type_id = t.id) "
    "+ (SELECT count(*) FROM bond_issues b WHERE b.type_id = t.id) AS in_use"
)
_TYPES = text(f"SELECT {_TYPE_COLUMNS} FROM product_types t ORDER BY t.built_in DESC, t.name")  # noqa: S608 - constant columns
_TYPE = text(f"SELECT {_TYPE_COLUMNS} FROM product_types t WHERE t.code = :code")  # noqa: S608 - constant columns
_LOCK_TYPE = text("SELECT id FROM product_types WHERE code = :code FOR UPDATE")
_CHANGE_COLUMNS = (
    "c.id, c.action, c.base_version, coalesce(c.code, t.code) AS type_code, "
    "coalesce(c.model, t.model) AS type_model, c.name, c.description, c.default_tax_bps, "
    "c.reason, c.status, c.proposed_by, c.proposed_at, c.decided_by, c.decided_at, "
    "c.decision_note"
)
_CHANGE_FROM = "FROM product_type_changes c LEFT JOIN product_types t ON t.id = c.type_id"
_CHANGES = text(
    f"SELECT {_CHANGE_COLUMNS} {_CHANGE_FROM} "
    "WHERE (CAST(:status AS text) IS NULL OR c.status = :status) "
    "ORDER BY c.proposed_at DESC, c.id DESC LIMIT :limit"
)
_CHANGE = text(f"SELECT {_CHANGE_COLUMNS} {_CHANGE_FROM} WHERE c.id = :id")
_LOCK_CHANGE = text("SELECT type_id FROM product_type_changes WHERE id = :id FOR UPDATE")
_TAKEN = text(
    "SELECT EXISTS (SELECT FROM product_types WHERE code = :code) "
    "OR EXISTS (SELECT FROM product_type_changes WHERE code = :code AND action = 'create' "
    "AND status = 'proposed' AND (CAST(:except AS text) IS NULL OR id <> :except)) AS code, "
    "EXISTS (SELECT FROM product_types WHERE lower(name) = lower(:name) "
    "AND (CAST(:type_id AS text) IS NULL OR id <> :type_id)) "
    "OR EXISTS (SELECT FROM product_type_changes WHERE lower(name) = lower(:name) "
    "AND action IN ('create', 'edit') AND status = 'proposed' "
    "AND (CAST(:except AS text) IS NULL OR id <> :except) "
    "AND (CAST(:type_id AS text) IS NULL OR type_id IS DISTINCT FROM :type_id)) AS name"
)
_WAITING = text(
    "SELECT EXISTS (SELECT FROM product_type_changes WHERE type_id = :type_id "
    "AND status = 'proposed')"
)
_INSERT_CHANGE = text(
    "INSERT INTO product_type_changes (id, action, type_id, base_version, code, model, name, "
    "description, default_tax_bps, reason, proposed_by, proposed_at) VALUES (:id, :action, "
    ":type_id, :base_version, :code, :model, :name, :description, :default_tax_bps, :reason, "
    ":proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE product_type_changes SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note WHERE id = :id"
)
_INSERT_TYPE = text(
    "INSERT INTO product_types (id, code, name, model, description, default_tax_bps, status, "
    "built_in, version, change_id, created_at, updated_at) VALUES (:id, :code, :name, :model, "
    ":description, :default_tax_bps, 'active', false, 1, :change_id, :now, :now)"
)
_UPDATE_TYPE = text(
    "UPDATE product_types SET name = :name, description = :description, "
    "default_tax_bps = :default_tax_bps, status = :status, version = version + 1, "
    "change_id = :change_id, updated_at = :now WHERE id = :id AND version = :version"
)
_ADVISORY = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")


# For other modules ------------------------------------------------------------------------------


async def type_for(
    session: AsyncSession, code: str | None, model: ReturnModel, *, field: str = "type_code"
) -> TypeView:
    """The type a new product or issue names, checked: in use and of `model`. With no code,
    the built-in type of the model. `validation_failed` on `field` otherwise."""
    found = await _type_or_none(session, code or BUILT_IN[model])
    if found is None:
        raise invalid(field, "unknown_type")
    if found.model is not model:
        raise invalid(field, "wrong_model")
    if found.status is not TypeStatus.ACTIVE:
        raise invalid(field, "type_retired")
    return found


# Reads ------------------------------------------------------------------------------------------


async def list_types(session: AsyncSession) -> list[TypeView]:
    """Every type, built-in first, then by name. Retired ones too: products of them still show."""
    return [_type_view(row) for row in (await session.execute(_TYPES)).all()]


async def get_type(session: AsyncSession, code: str) -> TypeView:
    found = await _type_or_none(session, code)
    if found is None:
        raise ApplicationError("not_found")
    return found


async def list_changes(
    session: AsyncSession, staff: Actor, status: ChangeStatus | None, limit: int = MAX_CHANGES
) -> list[ChangeView]:
    _staff_only(staff)
    rows = await session.execute(
        _CHANGES,
        {"status": status.value if status else None, "limit": max(1, min(limit, MAX_CHANGES))},
    )
    return [_change_view(row) for row in rows.all()]


# Proposals --------------------------------------------------------------------------------------


async def propose_type(
    session: AsyncSession, staff: Actor, new: NewType, reason: str, clock: Clock
) -> ChangeView:
    """Propose a new type. It is in use once a second person approves."""
    _staff_only(staff)
    _reason(reason)
    await session.execute(_ADVISORY, {"lock": f"finvest.product_types:code:{new.code}"})
    code_taken, name_taken = await _taken(session, new.code, new.terms.name)
    refusal = check_new(new, code_taken=code_taken, name_taken=name_taken)
    if refusal is not None:
        raise _refused(refusal)
    return await _save(
        session, staff, TypeAction.CREATE, None, None, new.code, new.model, new.terms, reason, clock
    )


async def propose_edit(
    session: AsyncSession, staff: Actor, code: str, terms: TypeTerms, reason: str, clock: Clock
) -> ChangeView:
    """Propose a new name, description or usual tax. Products already made keep their tax."""
    _staff_only(staff)
    _reason(reason)
    current = await _locked(session, code)
    _, name_taken = await _taken(session, None, terms.name, type_id=current.id)
    refusal = check_edit(current.terms, terms, name_taken=name_taken)
    if refusal is not None:
        raise _refused(refusal)
    return await _save(
        session, staff, TypeAction.EDIT, current, current.version, None, None, terms, reason, clock
    )


async def propose_status(
    session: AsyncSession,
    staff: Actor,
    code: str,
    action: TypeAction,
    reason: str,
    clock: Clock,
) -> ChangeView:
    """Propose retiring a type (no new products of it) or restoring it."""
    _staff_only(staff)
    _reason(reason)
    if action not in (TypeAction.RETIRE, TypeAction.RESTORE):
        raise invalid("action", "unknown_action")
    current = await _locked(session, code)
    refusal = check_status(current.status, action)
    if refusal is not None:
        raise _refused(refusal)
    return await _save(
        session, staff, action, current, current.version, None, None, None, reason, clock
    )


# Decisions --------------------------------------------------------------------------------------


async def approve(session: AsyncSession, staff: Actor, change_id: str, clock: Clock) -> ChangeView:
    """Approve as the second person. The type is written in this same transaction."""
    view, type_id = await _for_decision(session, staff, change_id)
    now = clock.now()
    current: TypeView | None = None
    if view.action is TypeAction.CREATE:
        assert view.terms is not None
        await session.execute(_ADVISORY, {"lock": f"finvest.product_types:code:{view.code}"})
        code_taken, name_taken = await _taken(
            session, view.code, view.terms.name, except_change=view.id
        )
        refusal = check_new(
            NewType(view.code, view.model, view.terms), code_taken=code_taken, name_taken=name_taken
        )
    else:
        current = await _locked(session, view.code)
        if current.id != type_id or current.version != view.base_version:
            raise _refused(Refusal.STALE)
        if view.action is TypeAction.EDIT:
            assert view.terms is not None
            _, name_taken = await _taken(
                session, None, view.terms.name, type_id=current.id, except_change=view.id
            )
            refusal = check_edit(current.terms, view.terms, name_taken=name_taken)
        else:
            refusal = check_status(current.status, view.action)
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {"id": view.id, "status": "approved", "decided_by": staff.id, "now": now, "note": None},
    )
    if current is None:
        assert view.terms is not None
        await session.execute(
            _INSERT_TYPE,
            {
                **_terms(view.terms),
                "id": push_id(),
                "code": view.code,
                "model": view.model.value,
                "change_id": view.id,
                "now": now,
            },
        )
    else:
        terms = view.terms or current.terms
        updated = await session.execute(
            _UPDATE_TYPE,
            {
                **_terms(terms),
                "id": current.id,
                "status": status_after(view.action, current.status).value,
                "version": current.version,
                "change_id": view.id,
                "now": now,
            },
        )
        if updated.rowcount != 1:  # type: ignore[attr-defined]
            raise ApplicationError("conflict")
    decided = await _change(session, view.id)
    await _audit(
        session,
        staff,
        "approval",
        "product_types.change.approved",
        decided,
        clock,
        reason=decided.reason,
    )
    return decided


async def send_back(
    session: AsyncSession, staff: Actor, change_id: str, note: str, clock: Clock
) -> ChangeView:
    if not valid_text(note):
        raise _refused(Refusal.NOTE_REQUIRED)
    view, _ = await _for_decision(session, staff, change_id)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": "sent_back",
            "decided_by": staff.id,
            "now": clock.now(),
            "note": note.strip(),
        },
    )
    decided = await _change(session, view.id)
    await _audit(
        session, staff, "approval", "product_types.change.sent_back", decided, clock, reason=note
    )
    return decided


async def withdraw(session: AsyncSession, staff: Actor, change_id: str, clock: Clock) -> ChangeView:
    _staff_only(staff)
    if (await session.execute(_LOCK_CHANGE, {"id": change_id})).one_or_none() is None:
        raise ApplicationError("not_found")
    view = await _change(session, change_id)
    refusal = check_withdrawal(status=view.status, proposed_by=view.proposed_by, by=_id(staff))
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": "withdrawn",
            "decided_by": staff.id,
            "now": clock.now(),
            "note": None,
        },
    )
    decided = await _change(session, view.id)
    await _audit(session, staff, "change", "product_types.change.withdrawn", decided, clock)
    return decided


# Internals --------------------------------------------------------------------------------------


def invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    if actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


def _reason(reason: str) -> None:
    if not valid_text(reason):
        raise _refused(Refusal.REASON_REQUIRED)


async def _type_or_none(session: AsyncSession, code: str) -> TypeView | None:
    row = (await session.execute(_TYPE, {"code": code})).one_or_none()
    return None if row is None else _type_view(row)


async def _locked(session: AsyncSession, code: str) -> TypeView:
    if (await session.execute(_LOCK_TYPE, {"code": code})).one_or_none() is None:
        raise ApplicationError("not_found")
    return await get_type(session, code)


async def _taken(
    session: AsyncSession,
    code: str | None,
    name: str,
    *,
    type_id: str | None = None,
    except_change: str | None = None,
) -> tuple[bool, bool]:
    row = (
        await session.execute(
            _TAKEN,
            {"code": code or "", "name": name, "type_id": type_id, "except": except_change},
        )
    ).one()
    return bool(row.code) and code is not None, bool(row.name)


async def _save(
    session: AsyncSession,
    staff: Actor,
    action: TypeAction,
    current: TypeView | None,
    base_version: int | None,
    code: str | None,
    model: ReturnModel | None,
    terms: TypeTerms | None,
    reason: str,
    clock: Clock,
) -> ChangeView:
    if (
        current is not None
        and (await session.execute(_WAITING, {"type_id": current.id})).scalar_one()
    ):
        raise _refused(Refusal.ALREADY_WAITING)
    change_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_CHANGE,
                {
                    **(_terms(terms) if terms else dict.fromkeys(_terms_keys())),
                    "id": change_id,
                    "action": action.value,
                    "type_id": current.id if current else None,
                    "base_version": base_version,
                    "code": code,
                    "model": model.value if model else None,
                    "reason": reason.strip(),
                    "proposed_by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # Another change to this type, or this new code, is already waiting.
        raise _refused(Refusal.ALREADY_WAITING) from error
    view = await _change(session, change_id)
    await _audit(
        session, staff, "change", "product_types.change.proposed", view, clock, reason=reason
    )
    return view


async def _for_decision(
    session: AsyncSession, staff: Actor, change_id: str
) -> tuple[ChangeView, str | None]:
    _staff_only(staff)
    locked = (await session.execute(_LOCK_CHANGE, {"id": change_id})).one_or_none()
    if locked is None:
        raise ApplicationError("not_found")
    view = await _change(session, change_id)
    refusal = check_decision(
        status=view.status, proposed_by=view.proposed_by, decided_by=_id(staff)
    )
    if refusal is not None:
        raise _refused(refusal)
    return view, locked.type_id


async def _change(session: AsyncSession, change_id: str) -> ChangeView:
    row = (await session.execute(_CHANGE, {"id": change_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _change_view(row)


def _terms_keys() -> tuple[str, ...]:
    return ("name", "description", "default_tax_bps")


def _terms(terms: TypeTerms) -> dict[str, object]:
    return {
        "name": terms.name,
        "description": terms.description.strip(),
        "default_tax_bps": terms.default_tax_bps,
    }


def _type_view(row: Row[Any]) -> TypeView:
    return TypeView(
        id=row.id,
        code=row.code,
        model=ReturnModel(row.model),
        terms=TypeTerms(row.name, row.description, row.default_tax_bps),
        status=TypeStatus(row.status),
        built_in=row.built_in,
        version=row.version,
        in_use=int(row.in_use),
    )


def _change_view(row: Row[Any]) -> ChangeView:
    terms = None if row.name is None else TypeTerms(row.name, row.description, row.default_tax_bps)
    return ChangeView(
        id=row.id,
        action=TypeAction(row.action),
        code=row.type_code,
        model=ReturnModel(row.type_model),
        terms=terms,
        base_version=row.base_version,
        reason=row.reason,
        status=ChangeStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


_FIELDS = {
    Refusal.INVALID_CODE: "code",
    Refusal.INVALID_NAME: "name",
    Refusal.INVALID_DESCRIPTION: "description",
    Refusal.INVALID_TAX: "default_tax_bps",
    Refusal.CODE_TAKEN: "code",
    Refusal.NAME_TAKEN: "name",
    Refusal.REASON_REQUIRED: "reason",
    Refusal.NOTE_REQUIRED: "note",
    Refusal.NOTHING_CHANGES: "name",
    Refusal.ALREADY_RETIRED: "action",
    Refusal.NOT_RETIRED: "action",
    Refusal.STALE: "type",
}


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal in _FIELDS:
        return invalid(_FIELDS[refusal], refusal.value)
    if refusal in (Refusal.NOT_WAITING, Refusal.ALREADY_WAITING):
        return ApplicationError("conflict")
    return ApplicationError("forbidden")


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    view: ChangeView,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    # Product types are business settings, not personal data.
    details: dict[str, object] = {
        "type": view.code,
        "model": view.model.value,
        "action": view.action.value,
    }
    if view.terms is not None:
        details.update(name=view.terms.name, default_tax_bps=view.terms.default_tax_bps)
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=CHANGE_RESOURCE,
            resource_id=view.id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details,
        ),
        clock,
    )
