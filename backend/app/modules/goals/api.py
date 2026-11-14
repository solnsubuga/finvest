"""Goal routes for the client app and the console.

Amounts are integers in the smallest unit with their currency, never decimals. Client
responses carry figures after tax only, and never a tax rate.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.modules.autoinvest.domain.plans import PlanStatus
from app.modules.goals import groups, saving_rules, service
from app.modules.goals.domain.goals import (
    MAX_NAME_LENGTH,
    GoalCategory,
    GoalStatus,
    Progress,
    Standing,
)
from app.modules.goals.domain.groups import MAX_DISPLAY_NAME_LENGTH, GroupProgress, GroupStatus
from app.modules.identity.api import CurrentClient, require_permission
from app.modules.identity.service import StaffSession
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.payments.domain.deposits import Network
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

client_router = APIRouter(prefix="/client/goals", tags=["goals"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff/goals", tags=["goals"], responses=_REFUSALS)
group_router = APIRouter(prefix="/client/goal-groups", tags=["goals"], responses=_REFUSALS)
staff_group_router = APIRouter(prefix="/staff/goal-groups", tags=["goals"], responses=_REFUSALS)
StaffWithView = Annotated[StaffSession, Depends(require_permission("goals.view"))]

AmountMinor = Annotated[int, Field(gt=0, le=10**15)]
# JSON has no date or enum type, so these are read from their text forms. Amounts stay strict.
Day = Annotated[date, Field(strict=False)]
Category = Annotated[GoalCategory, Field(strict=False)]
CurrencyCode = Annotated[Currency, Field(strict=False)]
DisplayName = Annotated[str, Field(min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH * 2)]
# What `secrets.token_urlsafe(16)` makes: 22 characters of URL-safe base64. Sent in the body,
# never in the path, so it stays out of access logs.
InviteCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{22}$")]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


DatabaseDep = Annotated[Database, Depends(_database)]
ClockDep = Annotated[Clock, Depends(_clock)]


def _money(amount_minor: int, currency: Currency) -> Money:
    try:
        return Money(amount_minor, currency)
    except ValueError:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["amount_minor"], code="invalid")],
        ) from None


# Contracts --------------------------------------------------------------------------------------


class GoalDetails(Contract):
    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH * 2)]
    category: Category
    target_minor: AmountMinor
    currency: CurrencyCode = Currency.UGX
    # Leave out for a goal with no date, such as an emergency fund.
    target_date: Day | None = None


class NewSaving(Contract):
    amount_minor: AmountMinor
    currency: CurrencyCode = Currency.UGX


class NewSavingRule(Contract):
    amount_minor: AmountMinor
    currency: CurrencyCode = Currency.UGX
    # The day of the month, 1 to 31. In a shorter month the saving is on its last day.
    day: Annotated[int, Field(ge=1, le=31)]
    network: Annotated[Network, Field(strict=False)]


class SavingRuleView(Contract):
    """The monthly saving collected by itself, by a mobile money prompt on its day."""

    plan_id: PushId
    amount_minor: int
    currency: Currency
    day: int
    network: Network
    payer_msisdn: str
    status: PlanStatus
    # The next day a saving is collected. None while paused.
    next_run_on: date | None
    created_at: datetime


class PlanView(Contract):
    """An estimate at today's rates, after tax. Never a promise."""

    product_code: str
    product_name: str
    rate_percent: str
    term_days: int
    currency: Currency
    saving_minor: int
    savings: int
    first_on: date
    last_on: date
    put_in_minor: int
    interest_minor: int
    without_investing_minor: int
    raised_to_minimum: bool


class ProgressView(Contract):
    value_minor: int
    interest_minor: int
    awaiting_payment_minor: int
    percent: int
    standing: Standing


class SavingView(Contract):
    """One investment made for the goal. Figures after tax."""

    investment_id: PushId
    product_code: str
    product_name: str
    status: InvestmentStatus
    amount_minor: int
    value_minor: int
    interest_minor: int
    rate_percent: str
    at_maturity: AtMaturity | None
    pay_by: datetime
    start_date: date | None
    maturity_date: date | None


class GoalView(Contract):
    goal_id: PushId
    name: str
    category: GoalCategory
    currency: Currency
    target_minor: int
    target_date: date | None
    status: GoalStatus
    product_code: str
    created_at: datetime
    progress: ProgressView
    plan: PlanView | None
    savings: list[SavingView]
    saving_rule: SavingRuleView | None
    # The day of the month the client is paid and nudged to save, 1 to 31, or null.
    payday: int | None


class Payday(Contract):
    # 1 to 31, or null to stop the payday nudge.
    day: int | None


class GoalList(Contract):
    goals: list[GoalView]


class StaffSavingView(Contract):
    investment_id: PushId
    product_code: str
    status: InvestmentStatus
    amount_minor: int
    accrued_net_minor: int
    start_date: date | None
    maturity_date: date | None


class StaffGoalView(Contract):
    goal_id: PushId
    client_id: PushId
    name: str
    category: GoalCategory
    currency: Currency
    target_minor: int
    target_date: date | None
    status: GoalStatus
    product_code: str
    created_at: datetime
    closed_at: datetime | None


class StaffGoalList(Contract):
    goals: list[StaffGoalView]


class StaffGoalDetail(Contract):
    goal: StaffGoalView
    progress: ProgressView
    savings: list[StaffSavingView]


def _plan_view(plan: service.ClientPlan) -> PlanView:
    return PlanView(
        product_code=plan.product_code,
        product_name=plan.product_name,
        rate_percent=plan.rate_percent,
        term_days=plan.term_days,
        currency=plan.saving.currency,
        saving_minor=plan.saving.minor,
        savings=plan.savings,
        first_on=plan.first_on,
        last_on=plan.last_on,
        put_in_minor=plan.put_in.minor,
        interest_minor=plan.interest.minor,
        without_investing_minor=plan.without_investing.minor,
        raised_to_minimum=plan.raised_to_minimum,
    )


def _progress_view(progress: Progress) -> ProgressView:
    return ProgressView(
        value_minor=progress.value.minor,
        interest_minor=progress.interest.minor,
        awaiting_payment_minor=progress.awaiting_payment.minor,
        percent=progress.percent,
        standing=progress.standing,
    )


def _saving_view(saving: investments.ClientInvestment) -> SavingView:
    return SavingView(
        investment_id=saving.id,
        product_code=saving.product_code,
        product_name=saving.product_name,
        status=saving.status,
        amount_minor=saving.amount.minor,
        value_minor=saving.value.minor,
        interest_minor=saving.interest_so_far.minor,
        rate_percent=saving.rate_percent,
        at_maturity=saving.at_maturity,
        pay_by=saving.pay_by,
        start_date=saving.start_date,
        maturity_date=saving.maturity_date,
    )


def _goal_view(goal: service.ClientGoal) -> GoalView:
    return GoalView(
        goal_id=goal.id,
        name=goal.name,
        category=goal.category,
        currency=goal.target.currency,
        target_minor=goal.target.minor,
        target_date=goal.target_date,
        status=goal.status,
        product_code=goal.product_code,
        created_at=goal.created_at,
        progress=_progress_view(goal.progress),
        plan=None if goal.plan is None else _plan_view(goal.plan),
        savings=[_saving_view(s) for s in goal.savings],
        saving_rule=None if goal.saving_rule is None else _rule_view(goal.saving_rule),
        payday=goal.payday,
    )


def _rule_view(rule: saving_rules.ClientSavingRule) -> SavingRuleView:
    return SavingRuleView(
        plan_id=rule.plan_id,
        amount_minor=rule.amount.minor,
        currency=rule.amount.currency,
        day=rule.day,
        network=rule.network,
        payer_msisdn=rule.payer_msisdn,
        status=rule.status,
        next_run_on=rule.next_run_on,
        created_at=rule.created_at,
    )


def _staff_goal_view(goal: service.Goal) -> StaffGoalView:
    return StaffGoalView(
        goal_id=goal.id,
        client_id=goal.client_id,
        name=goal.name,
        category=goal.category,
        currency=goal.target.currency,
        target_minor=goal.target.minor,
        target_date=goal.target_date,
        status=goal.status,
        product_code=goal.product_code,
        created_at=goal.created_at,
        closed_at=goal.closed_at,
    )


# Client routes ----------------------------------------------------------------------------------


@client_router.get("/plan", responses={422: {"model": ErrorEnvelope}})
async def plan(
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    target_minor: Annotated[int, Query(gt=0, le=10**15)],
    target_date: date,
    currency: Currency = Currency.UGX,
) -> PlanView | None:
    """What to save each month to reach a target by a date, before the goal is saved.

    `null` when no product's term ends before the date.
    """
    async with database.actor_transaction(client.actor) as session:
        found = await service.client_plan(
            session, target=_money(target_minor, currency), target_date=target_date, clock=clock
        )
    return None if found is None else _plan_view(found)


@client_router.post("", status_code=201, responses={422: {"model": ErrorEnvelope}})
async def create_goal(
    body: GoalDetails,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.create_goal(
            session,
            client.actor,
            idempotency_key=idempotency_key,
            name=body.name,
            category=body.category,
            target=_money(body.target_minor, body.currency),
            target_date=body.target_date,
            clock=clock,
        )
    return _goal_view(goal)


@client_router.get("")
async def list_goals(client: CurrentClient, database: DatabaseDep, clock: ClockDep) -> GoalList:
    async with database.actor_transaction(client.actor) as session:
        goals = await service.client_goals(session, client.actor, clock)
    return GoalList(goals=[_goal_view(g) for g in goals])


@client_router.get("/{goal_id}")
async def get_goal(
    goal_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.client_goal(session, client.actor, goal_id, clock)
    return _goal_view(goal)


@client_router.put("/{goal_id}", responses={422: {"model": ErrorEnvelope}})
async def update_goal(
    goal_id: PushId,
    body: GoalDetails,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.update_goal(
            session,
            client.actor,
            goal_id,
            name=body.name,
            category=body.category,
            target=_money(body.target_minor, body.currency),
            target_date=body.target_date,
            clock=clock,
        )
    return _goal_view(goal)


@client_router.put("/{goal_id}/payday")
async def set_payday(
    goal_id: PushId,
    body: Payday,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
) -> GoalView:
    """Name the day the client is paid, so they are nudged to save that morning."""
    async with database.actor_transaction(client.actor) as session:
        goal = await service.set_payday(session, client.actor, goal_id, body.day, clock)
    return _goal_view(goal)


@client_router.post("/{goal_id}/close")
async def close_goal(
    goal_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GoalView:
    async with database.actor_transaction(client.actor) as session:
        goal = await service.close_goal(session, client.actor, goal_id, clock)
    return _goal_view(goal)


@client_router.post(
    "/{goal_id}/savings", status_code=201, responses={422: {"model": ErrorEnvelope}}
)
async def save_to_goal(
    goal_id: PushId,
    body: NewSaving,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> SavingView:
    """Confirm a saving: a new investment awaiting payment. Pay for it as for any investment."""
    async with database.actor_transaction(client.actor) as session:
        saving = await service.save_to_goal(
            session,
            client.actor,
            goal_id,
            idempotency_key=idempotency_key,
            amount=_money(body.amount_minor, body.currency),
            clock=clock,
        )
    return _saving_view(saving)


@client_router.post(
    "/{goal_id}/saving-rule", status_code=201, responses={422: {"model": ErrorEnvelope}}
)
async def set_up_saving_rule(
    goal_id: PushId,
    body: NewSavingRule,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> GoalView:
    """Save the amount towards the goal on that day of every month, by itself.

    On each day the client's own number gets a mobile money prompt for the amount, which they
    approve as for any payment. 409 if the goal already has a rule or is closed. Answers with
    the goal, which carries the rule.
    """
    async with database.actor_transaction(client.actor) as session:
        await saving_rules.set_up_rule(
            session,
            client.actor,
            goal_id,
            amount=_money(body.amount_minor, body.currency),
            day=body.day,
            network=body.network,
            idempotency_key=idempotency_key,
            account_phone=client.phone,
            clock=clock,
        )
        goal = await service.client_goal(session, client.actor, goal_id, clock)
    return _goal_view(goal)


@client_router.post("/{goal_id}/saving-rule/stop")
async def stop_saving_rule(
    goal_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GoalView:
    """End the goal's monthly saving. Savings already made carry on. Sending it again changes
    nothing."""
    async with database.actor_transaction(client.actor) as session:
        goal = await service.client_goal(session, client.actor, goal_id, clock)
        await saving_rules.end_rule(session, client.actor, goal.id, clock)
        goal = await service.client_goal(session, client.actor, goal_id, clock)
    return _goal_view(goal)


# Staff routes -----------------------------------------------------------------------------------


@staff_router.get("")
async def staff_list_goals(
    staff: StaffWithView,
    database: DatabaseDep,
    client_id: PushId | None = None,
    status: GoalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = service.DEFAULT_PAGE_SIZE,
) -> StaffGoalList:
    async with database.actor_transaction(staff.actor) as session:
        goals = await service.list_goals(
            session, staff.actor, client_id=client_id, status=status, limit=limit
        )
    return StaffGoalList(goals=[_staff_goal_view(g) for g in goals])


@staff_router.get("/{goal_id}")
async def staff_get_goal(
    goal_id: PushId, staff: StaffWithView, database: DatabaseDep, clock: ClockDep
) -> StaffGoalDetail:
    async with database.actor_transaction(staff.actor) as session:
        found = await service.staff_goal(session, staff.actor, goal_id, clock)
    return StaffGoalDetail(
        goal=_staff_goal_view(found.goal),
        progress=_progress_view(found.progress),
        savings=[
            StaffSavingView(
                investment_id=s.id,
                product_code=s.product_code,
                status=s.status,
                amount_minor=s.amount.minor,
                accrued_net_minor=s.accrued_net.minor,
                start_date=s.start_date,
                maturity_date=s.maturity_date,
            )
            for s in found.savings
        ],
    )


# Group goals -------------------------------------------------------------------------------------


class NewGroup(GoalDetails):
    # How the other members see the client, as they write it.
    display_name: DisplayName
    # The client's own part: the target of their own goal in the group.
    own_target_minor: AmountMinor


class Invite(Contract):
    code: InviteCode


class JoinGroup(Contract):
    code: InviteCode
    display_name: DisplayName
    own_target_minor: AmountMinor
    currency: CurrencyCode = Currency.UGX


class Rename(Contract):
    display_name: DisplayName


class GroupProgressView(Contract):
    value_minor: int
    percent: int
    standing: Standing


class MemberView(Contract):
    """Another member: a name and when they joined. Never what they saved."""

    display_name: str
    joined_at: datetime
    is_you: bool
    is_owner: bool
    # Only for the one who set the group up, and only for the others: send it to remove them.
    member_id: PushId | None


class GroupView(Contract):
    group_id: PushId
    name: str
    category: GoalCategory
    currency: Currency
    target_minor: int
    target_date: date | None
    status: GroupStatus
    created_at: datetime
    is_owner: bool
    own_goal_id: PushId
    progress: GroupProgressView
    # The client's own goal is counted as it is now; the others as of this day.
    others_as_of: date | None
    members: list[MemberView]
    # Only for the one who set the group up, while an invite is open.
    invite_expires_at: datetime | None


class GroupList(Contract):
    groups: list[GroupView]


class InviteView(Contract):
    """Shown once. Only its hash is kept, so it cannot be fetched again."""

    code: str
    expires_at: datetime


class InvitePreview(Contract):
    group_id: PushId
    name: str
    category: GoalCategory
    currency: Currency
    target_minor: int
    target_date: date | None
    owner_name: str
    members: int
    expires_at: datetime


class StaffMemberView(Contract):
    goal_id: PushId
    client_id: PushId
    display_name: str
    joined_at: datetime
    left_at: datetime | None
    value_minor: int
    value_on: date | None


class StaffGroupView(Contract):
    group_id: PushId
    owner_client_id: PushId
    name: str
    category: GoalCategory
    currency: Currency
    target_minor: int
    target_date: date | None
    status: GroupStatus
    created_at: datetime
    closed_at: datetime | None


class StaffGroupList(Contract):
    groups: list[StaffGroupView]


class StaffGroupDetail(Contract):
    group: StaffGroupView
    progress: GroupProgressView
    members: list[StaffMemberView]


def _group_progress_view(progress: GroupProgress) -> GroupProgressView:
    return GroupProgressView(
        value_minor=progress.value.minor, percent=progress.percent, standing=progress.standing
    )


def _group_view(group: groups.ClientGroup) -> GroupView:
    return GroupView(
        group_id=group.id,
        name=group.name,
        category=group.category,
        currency=group.target.currency,
        target_minor=group.target.minor,
        target_date=group.target_date,
        status=group.status,
        created_at=group.created_at,
        is_owner=group.is_owner,
        own_goal_id=group.own_goal_id,
        progress=_group_progress_view(group.progress),
        others_as_of=group.others_as_of,
        members=[
            MemberView(
                display_name=m.display_name,
                joined_at=m.joined_at,
                is_you=m.is_you,
                is_owner=m.is_owner,
                member_id=m.member_id,
            )
            for m in group.members
        ],
        invite_expires_at=group.invite_expires_at,
    )


def _staff_group_view(group: groups.Group) -> StaffGroupView:
    return StaffGroupView(
        group_id=group.id,
        owner_client_id=group.owner_client_id,
        name=group.name,
        category=group.category,
        currency=group.target.currency,
        target_minor=group.target.minor,
        target_date=group.target_date,
        status=group.status,
        created_at=group.created_at,
        closed_at=group.closed_at,
    )


@group_router.post("", status_code=201, responses={422: {"model": ErrorEnvelope}})
async def create_group(
    body: NewGroup,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> GroupView:
    """Set up a group goal. The client's own part is a goal of their own, with the group's date
    and `own_target_minor` as its target, which they save into like any goal."""
    async with database.actor_transaction(client.actor) as session:
        group = await groups.create_group(
            session,
            client.actor,
            idempotency_key=idempotency_key,
            name=body.name,
            category=body.category,
            target=_money(body.target_minor, body.currency),
            target_date=body.target_date,
            display_name=body.display_name,
            own_target=_money(body.own_target_minor, body.currency),
            clock=clock,
        )
    return _group_view(group)


@group_router.get("")
async def list_groups(client: CurrentClient, database: DatabaseDep, clock: ClockDep) -> GroupList:
    async with database.actor_transaction(client.actor) as session:
        found = await groups.client_groups(session, client.actor, clock)
    return GroupList(groups=[_group_view(g) for g in found])


@group_router.post("/invites/preview")
async def preview_invite(
    body: Invite, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> InvitePreview:
    """The group an invite is for. Not found for a code that is wrong, replaced or expired.

    A POST so the code travels in the body: a path or query would put it in access logs."""
    async with database.actor_transaction(client.actor) as session:
        invite = await groups.preview_invite(session, client.actor, body.code, clock)
    return InvitePreview(
        group_id=invite.group_id,
        name=invite.name,
        category=invite.category,
        currency=invite.target.currency,
        target_minor=invite.target.minor,
        target_date=invite.target_date,
        owner_name=invite.owner_name,
        members=invite.members,
        expires_at=invite.expires_at,
    )


@group_router.post("/join", status_code=201, responses={422: {"model": ErrorEnvelope}})
async def join_group(
    body: JoinGroup,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
    idempotency_key: IdempotencyKey,
) -> GroupView:
    async with database.actor_transaction(client.actor) as session:
        group = await groups.join_group(
            session,
            client.actor,
            body.code,
            idempotency_key=idempotency_key,
            display_name=body.display_name,
            own_target=_money(body.own_target_minor, body.currency),
            clock=clock,
        )
    return _group_view(group)


@group_router.get("/{group_id}")
async def get_group(
    group_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GroupView:
    async with database.actor_transaction(client.actor) as session:
        group = await groups.client_group(session, client.actor, group_id, clock)
    return _group_view(group)


@group_router.put("/{group_id}", responses={422: {"model": ErrorEnvelope}})
async def update_group(
    group_id: PushId,
    body: GoalDetails,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
) -> GroupView:
    """Change the group's name, target or date. For the one who set it up."""
    async with database.actor_transaction(client.actor) as session:
        group = await groups.update_group(
            session,
            client.actor,
            group_id,
            name=body.name,
            category=body.category,
            target=_money(body.target_minor, body.currency),
            target_date=body.target_date,
            clock=clock,
        )
    return _group_view(group)


@group_router.put("/{group_id}/me", responses={422: {"model": ErrorEnvelope}})
async def rename_self(
    group_id: PushId,
    body: Rename,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
) -> GroupView:
    """Change the name the other members see the client by."""
    async with database.actor_transaction(client.actor) as session:
        group = await groups.rename_self(
            session, client.actor, group_id, display_name=body.display_name, clock=clock
        )
    return _group_view(group)


@group_router.post("/{group_id}/invite", status_code=201)
async def new_invite(
    group_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> InviteView:
    """A new invite code, replacing any earlier one. Shown once."""
    async with database.actor_transaction(client.actor) as session:
        invite = await groups.new_invite(session, client.actor, group_id, clock)
    return InviteView(code=invite.code, expires_at=invite.expires_at)


@group_router.delete("/{group_id}/invite")
async def revoke_invite(
    group_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GroupView:
    async with database.actor_transaction(client.actor) as session:
        group = await groups.revoke_invite(session, client.actor, group_id, clock)
    return _group_view(group)


@group_router.post("/{group_id}/leave", status_code=204)
async def leave_group(
    group_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> Response:
    """Leave the group. The client's own goal and its savings carry on as theirs."""
    async with database.actor_transaction(client.actor) as session:
        await groups.leave_group(session, client.actor, group_id, clock)
    return Response(status_code=204)


@group_router.post("/{group_id}/members/{member_id}/remove")
async def remove_member(
    group_id: PushId,
    member_id: PushId,
    client: CurrentClient,
    database: DatabaseDep,
    clock: ClockDep,
) -> GroupView:
    async with database.actor_transaction(client.actor) as session:
        group = await groups.remove_member(session, client.actor, group_id, member_id, clock)
    return _group_view(group)


@group_router.post("/{group_id}/close")
async def close_group(
    group_id: PushId, client: CurrentClient, database: DatabaseDep, clock: ClockDep
) -> GroupView:
    """Close the group for good. Every member's goal carries on as their own."""
    async with database.actor_transaction(client.actor) as session:
        group = await groups.close_group(session, client.actor, group_id, clock)
    return _group_view(group)


@staff_group_router.get("")
async def staff_list_groups(
    staff: StaffWithView,
    database: DatabaseDep,
    status: GroupStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=groups.MAX_PAGE_SIZE)] = groups.DEFAULT_PAGE_SIZE,
) -> StaffGroupList:
    async with database.actor_transaction(staff.actor) as session:
        found = await groups.list_groups(session, staff.actor, status=status, limit=limit)
    return StaffGroupList(groups=[_staff_group_view(g) for g in found])


@staff_group_router.get("/{group_id}")
async def staff_get_group(
    group_id: PushId, staff: StaffWithView, database: DatabaseDep, clock: ClockDep
) -> StaffGroupDetail:
    async with database.actor_transaction(staff.actor) as session:
        found = await groups.staff_group(session, staff.actor, group_id, clock)
    return StaffGroupDetail(
        group=_staff_group_view(found.group),
        progress=_group_progress_view(found.progress),
        members=[
            StaffMemberView(
                goal_id=m.goal_id,
                client_id=m.client_id,
                display_name=m.display_name,
                joined_at=m.joined_at,
                left_at=m.left_at,
                value_minor=m.value.minor,
                value_on=m.value_on,
            )
            for m in found.members
        ],
    )


routers: list[APIRouter] = [client_router, staff_router, group_router, staff_group_router]
