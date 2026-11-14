"""Routes for a client's next of kin and their answers to the investing questions.

A client reads and replaces their own next of kin, and answers the questions. Staff who may see
clients read both on the client's page; seeing next of kin is written to the audit trail and the
ID numbers stay masked. Without the identity data key the next of kin routes answer
`unavailable`.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity.api import CurrentClient, require_permission
from app.modules.identity.service import StaffSession
from app.modules.kyc import next_of_kin, risk
from app.modules.kyc.domain.checks import mask_document_number
from app.modules.kyc.domain.next_of_kin import MAX_NAME, MAX_PEOPLE, Person, Relationship
from app.modules.kyc.domain.risk import LONGEST_TERM_DAYS, QUESTIONS, Profile
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 503)
}
Kin = Annotated[Relationship, Field(strict=False)]


class PersonBody(Contract):
    full_name: str = Field(max_length=MAX_NAME + 50)
    relationship: Kin
    phone: str = Field(max_length=32)
    id_number: str = Field(max_length=40)


class NextOfKinBody(Contract):
    people: list[PersonBody] = Field(max_length=MAX_PEOPLE + 5)


class PersonView(Contract):
    id: PushId
    full_name: str
    relationship: Relationship
    phone: str
    id_number: str


class NextOfKinView(Contract):
    people: list[PersonView]
    updated_at: datetime | None


class OptionView(Contract):
    code: str


class QuestionView(Contract):
    code: str
    options: list[OptionView]


class AnswersView(Contract):
    id: PushId
    answers: dict[str, str]
    profile: Profile
    # The longest term, in days, that suits the profile. None for any term.
    longest_term_days: int | None
    answered_at: datetime
    due_again_at: datetime


class RiskProfileView(Contract):
    questions: list[QuestionView]
    latest: AnswersView | None
    # True before the first answers and once a year has passed since the last.
    due: bool


class AnswersBody(Contract):
    answers: dict[str, str] = Field(max_length=len(QUESTIONS) + 2)


class StaffPersonView(Contract):
    full_name: str
    relationship: Relationship
    phone: str
    id_number_masked: str


class StaffProfileView(Contract):
    next_of_kin: list[StaffPersonView] | None
    next_of_kin_updated_at: datetime | None
    risk: list[AnswersView]


def _answers(found: risk.RiskAnswers) -> AnswersView:
    return AnswersView(
        id=found.id,
        answers=found.answers,
        profile=found.profile,
        longest_term_days=LONGEST_TERM_DAYS[found.profile],
        answered_at=found.answered_at,
        due_again_at=found.due_again_at,
    )


def _risk_profile(found: risk.RiskAnswers | None, now: datetime) -> RiskProfileView:
    return RiskProfileView(
        questions=[
            QuestionView(code=q.code, options=[OptionView(code=o.code) for o in q.options])
            for q in QUESTIONS
        ],
        latest=_answers(found) if found else None,
        due=found is None or found.due_again_at <= now,
    )


def _kin(found: next_of_kin.NextOfKin) -> NextOfKinView:
    return NextOfKinView(
        people=[
            PersonView(
                id=p.id,
                full_name=p.full_name,
                relationship=p.relationship,
                phone=p.phone,
                id_number=p.id_number,
            )
            for p in found.people
        ],
        updated_at=found.updated_at,
    )


def create_routers(sealer: Sealer | None) -> list[APIRouter]:
    client_router = APIRouter(prefix="/client", tags=["client profile"], responses=_REFUSALS)
    staff_router = APIRouter(prefix="/staff/clients", tags=["client profile"], responses=_REFUSALS)
    Viewer = Annotated[StaffSession, Depends(require_permission("clients.view"))]

    def seal() -> Sealer:
        if sealer is None:
            raise ApplicationError("unavailable")
        return sealer

    def database(request: Request) -> Database:
        held: Database = request.app.state.database
        return held

    def clock(request: Request) -> Clock:
        held: Clock = request.app.state.clock
        return held

    @client_router.get("/next-of-kin")
    async def my_next_of_kin(client: CurrentClient, request: Request) -> NextOfKinView:
        """The people the signed-in client has named, in their order."""
        async with database(request).actor_transaction(client.actor) as session:
            found = await next_of_kin.next_of_kin(session, seal(), client.client_id)
        return _kin(found)

    @client_router.put("/next-of-kin")
    async def save_next_of_kin(
        body: NextOfKinBody, client: CurrentClient, request: Request
    ) -> NextOfKinView:
        """Replace the whole list. Shares add up to 10,000 basis points, or the list is empty."""
        people = [
            Person(
                full_name=p.full_name,
                relationship=p.relationship,
                phone=p.phone,
                id_number=p.id_number,
            )
            for p in body.people
        ]
        async with database(request).actor_transaction(client.actor) as session:
            found = await next_of_kin.save(
                session, seal(), client.actor, people, clock(request), client_phone=client.phone
            )
        return _kin(found)

    @client_router.get("/risk-profile")
    async def my_risk_profile(client: CurrentClient, request: Request) -> RiskProfileView:
        """The questions, the client's latest answers, and whether they are due again."""
        async with database(request).actor_transaction(client.actor) as session:
            found = await risk.latest(session, client.client_id)
        return _risk_profile(found, clock(request).now())

    @client_router.put("/risk-profile")
    async def answer_risk_questions(
        body: AnswersBody, client: CurrentClient, request: Request
    ) -> RiskProfileView:
        """Answer every question. Earlier answers are kept on the record."""
        now = clock(request)
        async with database(request).actor_transaction(client.actor) as session:
            found = await risk.answer(session, client.actor, body.answers, now)
        return _risk_profile(found, now.now())

    @staff_router.get("/{client_id}/profile")
    async def client_profile(
        client_id: PushId, staff: Viewer, request: Request
    ) -> StaffProfileView:
        """A client's next of kin, with ID numbers masked, and every set of risk answers.

        Next of kin are None when the identity data key is not configured.
        """
        now = clock(request)
        async with database(request).actor_transaction(staff.actor) as session:
            kin = await next_of_kin.next_of_kin(session, sealer, client_id) if sealer else None
            answers = await risk.history(session, client_id)
            if kin is not None and kin.people:
                await audit.record(
                    session,
                    AuditEvent(
                        category="access",
                        action="kyc.next_of_kin.viewed",
                        resource_type="next_of_kin",
                        resource_id=client_id,
                        actor=AuditActor.staff(staff.staff_id),
                    ),
                    now,
                )
        return StaffProfileView(
            next_of_kin=[
                StaffPersonView(
                    full_name=p.full_name,
                    relationship=p.relationship,
                    phone=p.phone,
                    id_number_masked=mask_document_number(p.id_number),
                )
                for p in kin.people
            ]
            if kin is not None
            else None,
            next_of_kin_updated_at=kin.updated_at if kin else None,
            risk=[_answers(item) for item in answers],
        )

    return [client_router, staff_router]
