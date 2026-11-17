"""Product types over HTTP, for staff.

* `GET /staff/product-types`: every type with its return model, usual tax and how many
  products and bond issues use it, and the return models a new type can be built on.
* `/staff/product-type-changes`: proposing a new type, an edit, retiring or restoring one, and
  deciding, a second person enforced in the database.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.product_types import service
from app.modules.product_types.domain.types import (
    MAX_CODE,
    MAX_DESCRIPTION,
    MAX_NAME,
    MODEL_DESCRIPTIONS,
    ChangeStatus,
    NewType,
    ReturnModel,
    TypeAction,
    TypeStatus,
    TypeTerms,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}

Code = Annotated[
    str, Field(min_length=1, max_length=MAX_CODE, pattern=r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$")
]
Name = Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
Description = Annotated[str, Field(min_length=1, max_length=MAX_DESCRIPTION)]
Bps = Annotated[int, Field(ge=0, le=9999)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
Note = Annotated[str, Field(min_length=1, max_length=500)]
# Sent as its value, such as "fixed_income". The strict `Contract` would otherwise take only an
# enum instance, which JSON can never carry.
ModelIn = Annotated[ReturnModel, Field(strict=False)]


class ReturnModelOut(Contract):
    model: ReturnModel
    description: str


class ProductTypeOut(Contract):
    code: str
    name: str
    model: ReturnModel
    description: str
    default_tax_bps: int
    status: TypeStatus
    built_in: bool
    # Products and bond issues of this type, of any status.
    in_use: int


class ProductTypesOut(Contract):
    types: list[ProductTypeOut]
    # What a new type can be built on. A new way of earning needs code first.
    models: list[ReturnModelOut]


class ProductTypeChangeOut(Contract):
    id: PushId
    action: TypeAction
    code: str
    model: ReturnModel
    name: str | None
    description: str | None
    default_tax_bps: int | None
    reason: str
    status: ChangeStatus
    proposed_by: PushId
    proposed_at: datetime
    decided_by: PushId | None
    decided_at: datetime | None
    decision_note: str | None


class NewTypeIn(Contract):
    code: Code
    model: ModelIn
    name: Name
    description: Description
    default_tax_bps: Bps
    reason: Reason


class EditTypeIn(Contract):
    code: Code
    name: Name
    description: Description
    default_tax_bps: Bps
    reason: Reason


class TypeStatusIn(Contract):
    code: Code
    to: Literal["active", "retired"]
    reason: Reason


class SendBack(Contract):
    note: Note


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
ProductsViewer = Annotated[StaffSession, Depends(require_permission("products.view"))]
ProductsChanger = Annotated[StaffSession, Depends(require_permission("products.change"))]
ProductsApprover = Annotated[StaffSession, Depends(require_confirmed("products.change"))]


@asynccontextmanager
async def _transaction(request: Request, actor: Actor) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.actor_transaction(actor) as session:
        yield session


def _type_out(view: service.TypeView) -> ProductTypeOut:
    return ProductTypeOut(
        code=view.code,
        name=view.terms.name,
        model=view.model,
        description=view.terms.description,
        default_tax_bps=view.terms.default_tax_bps,
        status=view.status,
        built_in=view.built_in,
        in_use=view.in_use,
    )


def _change_out(view: service.ChangeView) -> ProductTypeChangeOut:
    terms = view.terms
    return ProductTypeChangeOut(
        id=view.id,
        action=view.action,
        code=view.code,
        model=view.model,
        name=terms.name if terms else None,
        description=terms.description if terms else None,
        default_tax_bps=terms.default_tax_bps if terms else None,
        reason=view.reason,
        status=view.status,
        proposed_by=view.proposed_by,
        proposed_at=view.proposed_at,
        decided_by=view.decided_by,
        decided_at=view.decided_at,
        decision_note=view.decision_note,
    )


staff_router = APIRouter(prefix="/staff", tags=["staff product types"], responses=_REFUSALS)


@staff_router.get("/product-types")
async def staff_product_types(request: Request, staff: ProductsViewer) -> ProductTypesOut:
    async with _transaction(request, staff.actor) as session:
        found = await service.list_types(session)
    return ProductTypesOut(
        types=[_type_out(t) for t in found],
        models=[ReturnModelOut(model=m, description=d) for m, d in MODEL_DESCRIPTIONS.items()],
    )


@staff_router.get("/product-type-changes")
async def staff_product_type_changes(
    request: Request, staff: ProductsViewer, status: ChangeStatus | None = None
) -> list[ProductTypeChangeOut]:
    async with _transaction(request, staff.actor) as session:
        found = await service.list_changes(session, staff.actor, status)
    return [_change_out(c) for c in found]


@staff_router.post("/product-type-changes/new-type", status_code=201)
async def staff_propose_product_type(
    request: Request, staff: ProductsChanger, clock: AppClock, body: NewTypeIn
) -> ProductTypeChangeOut:
    """Propose a new type built on a return model. In use once a second person approves."""
    terms = TypeTerms(body.name, body.description, body.default_tax_bps)
    new = NewType(body.code, body.model, terms)
    async with _transaction(request, staff.actor) as session:
        view = await service.propose_type(session, staff.actor, new, body.reason, clock)
    return _change_out(view)


@staff_router.post("/product-type-changes/edit", status_code=201)
async def staff_propose_product_type_edit(
    request: Request, staff: ProductsChanger, clock: AppClock, body: EditTypeIn
) -> ProductTypeChangeOut:
    """Propose a new name, description or usual tax. The return model never changes."""
    terms = TypeTerms(body.name, body.description, body.default_tax_bps)
    async with _transaction(request, staff.actor) as session:
        view = await service.propose_edit(
            session, staff.actor, body.code, terms, body.reason, clock
        )
    return _change_out(view)


@staff_router.post("/product-type-changes/status", status_code=201)
async def staff_propose_product_type_status(
    request: Request, staff: ProductsChanger, clock: AppClock, body: TypeStatusIn
) -> ProductTypeChangeOut:
    """Propose retiring (`retired`: no new products of it) or restoring (`active`) a type."""
    action = TypeAction.RETIRE if body.to == "retired" else TypeAction.RESTORE
    async with _transaction(request, staff.actor) as session:
        view = await service.propose_status(
            session, staff.actor, body.code, action, body.reason, clock
        )
    return _change_out(view)


@staff_router.post("/product-type-changes/{change_id}/approve")
async def staff_approve_product_type_change(
    request: Request, staff: ProductsApprover, clock: AppClock, change_id: str
) -> ProductTypeChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.approve(session, staff.actor, change_id, clock)
    return _change_out(view)


@staff_router.post("/product-type-changes/{change_id}/send-back")
async def staff_send_back_product_type_change(
    request: Request, staff: ProductsChanger, clock: AppClock, change_id: str, body: SendBack
) -> ProductTypeChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.send_back(session, staff.actor, change_id, body.note, clock)
    return _change_out(view)


@staff_router.post("/product-type-changes/{change_id}/withdraw")
async def staff_withdraw_product_type_change(
    request: Request, staff: ProductsChanger, clock: AppClock, change_id: str
) -> ProductTypeChangeOut:
    async with _transaction(request, staff.actor) as session:
        view = await service.withdraw(session, staff.actor, change_id, clock)
    return _change_out(view)


routers = (staff_router,)
