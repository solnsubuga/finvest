"""The client's answers to the investing questions, and the profile they give.

Every function works inside the caller's transaction. Answers are only ever added: the latest
is the client's profile, and it is due again a year after it was given.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.kyc.domain.risk import AnswerProblem, Profile, due_again, profile_for, score
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

# A client changes their mind a few times at most. Past this many sets in a day, a new set is
# refused, so a loop in the app or a script cannot grow the client's record without end.
MAX_ANSWERS_A_DAY = 5
_A_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class RiskAnswers:
    id: str
    answers: dict[str, str]
    score: int
    profile: Profile
    answered_at: datetime
    due_again_at: datetime


_LATEST = text(
    "SELECT id, answers, score, profile, answered_at FROM kyc_risk_answers "
    "WHERE client_id = :client_id ORDER BY answered_at DESC, id DESC LIMIT 1"
)
_HISTORY = text(
    "SELECT id, answers, score, profile, answered_at FROM kyc_risk_answers "
    "WHERE client_id = :client_id ORDER BY answered_at DESC, id DESC"
)
_RECENT = text(
    "SELECT count(*) FROM kyc_risk_answers WHERE client_id = :client_id AND answered_at > :since"
)
_CLIENT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))")
_ADD = text(
    "INSERT INTO kyc_risk_answers (id, client_id, answers, score, profile, answered_at) "
    "VALUES (:id, :client_id, CAST(:answers AS jsonb), :score, :profile, :now)"
)


async def latest(session: AsyncSession, client_id: str) -> RiskAnswers | None:
    """The client's latest answers, or None before they first answer."""
    row = (await session.execute(_LATEST, {"client_id": client_id})).one_or_none()
    return _answers(row) if row else None


async def history(session: AsyncSession, client_id: str) -> list[RiskAnswers]:
    """Every set of answers the client has given, newest first."""
    return [_answers(row) for row in (await session.execute(_HISTORY, {"client_id": client_id}))]


async def answer(
    session: AsyncSession, client: Actor, answers: dict[str, str], clock: Clock
) -> RiskAnswers:
    """Record a full set of answers from the signed-in client, at most a few a day."""
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    try:
        points = score(answers)
        profile = profile_for(answers)
    except AnswerProblem as problem:
        raise ApplicationError(
            "validation_failed",
            fields=[
                FieldProblem(field_path=["body", "answers", problem.question], code=problem.code)
            ],
        ) from None
    # One set at a time per client, so two at once cannot both slip under the daily cap.
    await session.execute(_CLIENT_LOCK, {"lock": f"kyc_risk_answers:{client.id}"})
    recent = (
        await session.execute(_RECENT, {"client_id": client.id, "since": clock.now() - _A_DAY})
    ).scalar_one()
    if recent >= MAX_ANSWERS_A_DAY:
        raise ApplicationError("rate_limited")
    answers_id = push_id(clock)
    await session.execute(
        _ADD,
        {
            "id": answers_id,
            "client_id": client.id,
            "answers": json.dumps(answers, sort_keys=True),
            "score": points,
            "profile": profile.value,
            "now": clock.now(),
        },
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="kyc.risk_answers.given",
            resource_type="risk_answers",
            resource_id=answers_id,
            actor=AuditActor.client(client.id),
            details={"profile": profile.value},
        ),
        clock,
    )
    found = await latest(session, client.id)
    assert found is not None
    return found


def _answers(row: Row[Any]) -> RiskAnswers:
    return RiskAnswers(
        id=row.id,
        answers=dict(row.answers),
        score=row.score,
        profile=Profile(row.profile),
        answered_at=row.answered_at,
        due_again_at=due_again(row.answered_at),
    )
