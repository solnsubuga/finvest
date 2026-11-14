"""Routes for the platform's settings, on the console's Settings screen.

One member of staff proposes a value and a different one approves it. Until then the value in
use stays as it was. Each section of Settings has its own permission to change it, and seeing
every section is `settings.view`.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, TypeAdapter

from app.infrastructure.database import Database
from app.modules.identity.api import caller_address, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.platform_settings import service
from app.modules.platform_settings.domain.catalogue import (
    DATE_FORMATS,
    DEFINITIONS,
    MAX_COLUMN,
    MAX_DAYS_BEFORE,
    MAX_HOLIDAY_NAME,
    MAX_HOLIDAYS,
    MAX_IDLE_MINUTES,
    MAX_LAYOUT_LABEL,
    MAX_LAYOUTS,
    MAX_NETWORK_LABEL,
    MAX_NETWORKS,
    MAX_REMINDERS,
    MIN_IDLE_MINUTES,
    PERMISSIONS,
    SECTIONS,
    Key,
    Section,
)
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

staff_router = APIRouter(prefix="/staff", tags=["staff settings"], responses=_REFUSALS)
routers = [staff_router]

SettingsStaff = Annotated[StaffSession, Depends(require_permission("settings.view"))]
# Deciding needs a passkey or app code from the last few minutes. The section's own permission
# is checked once the proposal shows which setting it changes.
SettingsDecider = Annotated[StaffSession, Depends(require_confirmed("settings.view"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
ClockTime = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
Column = Annotated[str, Field(min_length=1, max_length=MAX_COLUMN)]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


# Values ---------------------------------------------------------------------------------------


class PaymentChannelsValue(Contract):
    """True where the channel is open. A paused channel refuses new requests only."""

    mtn_pay_in: bool
    airtel_pay_in: bool
    bank_pay_in: bool
    mtn_payout: bool
    airtel_payout: bool
    bank_payout: bool


class HolidayValue(Contract):
    date: Day
    name: Annotated[str, Field(min_length=1, max_length=MAX_HOLIDAY_NAME)]


class OfficeNetworkValue(Contract):
    # An address or a network, such as 196.43.130.0/24.
    network: Annotated[str, Field(min_length=1, max_length=50)]
    label: Annotated[str, Field(min_length=1, max_length=MAX_NETWORK_LABEL)]


DateFormat = Literal[
    "YYYY-MM-DD", "DD/MM/YYYY", "DD-MM-YYYY", "DD.MM.YYYY", "MM/DD/YYYY", "DD MMM YYYY",
    "DD-MMM-YYYY",
]  # fmt: skip


class StatementLayoutValue(Contract):
    """One provider's export. Give money_in and money_out, or amount, direction and its words."""

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
    label: Annotated[str, Field(min_length=1, max_length=MAX_LAYOUT_LABEL)]
    date_column: Column
    date_format: DateFormat
    narrative: Column
    reference: Column | None = None
    money_in: Column | None = None
    money_out: Column | None = None
    amount: Column | None = None
    direction: Column | None = None
    in_word: Column | None = None
    out_word: Column | None = None


Holidays = Annotated[list[HolidayValue], Field(max_length=MAX_HOLIDAYS)]
Reminders = Annotated[
    list[Annotated[int, Field(ge=1, le=MAX_DAYS_BEFORE)]], Field(max_length=MAX_REMINDERS)
]
IdleMinutes = Annotated[int, Field(ge=MIN_IDLE_MINUTES, le=MAX_IDLE_MINUTES)]
Networks = Annotated[list[OfficeNetworkValue], Field(max_length=MAX_NETWORKS)]
Layouts = Annotated[list[StatementLayoutValue], Field(max_length=MAX_LAYOUTS)]


# Proposals, as sent ---------------------------------------------------------------------------


class ProposePaymentChannels(Contract):
    key: Literal["payment_channels"]
    value: PaymentChannelsValue
    reason: Reason


class ProposePublicHolidays(Contract):
    key: Literal["public_holidays"]
    value: Holidays
    reason: Reason


class ProposeBankPayoutCutoff(Contract):
    key: Literal["bank_payout_cutoff"]
    # Kampala time, 24-hour, between 08:00 and 17:00.
    value: ClockTime
    reason: Reason


class ProposeMaturityReminders(Contract):
    key: Literal["maturity_reminders"]
    # Days before maturity. Empty sends no reminders.
    value: Reminders
    reason: Reason


class ProposeStaffIdleMinutes(Contract):
    key: Literal["staff_idle_minutes"]
    value: IdleMinutes
    reason: Reason


class ProposeOfficeNetworks(Contract):
    key: Literal["office_networks"]
    # Empty lets staff use the console from any network.
    value: Networks
    reason: Reason


class ProposeStatementLayouts(Contract):
    key: Literal["statement_layouts"]
    value: Layouts
    reason: Reason


ProposeSetting = Annotated[
    ProposePaymentChannels
    | ProposePublicHolidays
    | ProposeBankPayoutCutoff
    | ProposeMaturityReminders
    | ProposeStaffIdleMinutes
    | ProposeOfficeNetworks
    | ProposeStatementLayouts,
    Field(discriminator="key"),
]


class DecideSetting(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


# Settings, as listed --------------------------------------------------------------------------


class _Proposal(Contract):
    id: PushId
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    # The proposer can withdraw it. Anyone else who may change the setting can decide it.
    yours: bool


class _Setting(Contract):
    section: Section
    # None while the default is in use.
    changed_at: datetime | None
    changed_by: PushId | None
    # The reader may propose a change and decide other people's.
    can_change: bool


class PaymentChannelsProposal(_Proposal):
    value: PaymentChannelsValue


class PaymentChannelsSetting(_Setting):
    key: Literal["payment_channels"]
    value: PaymentChannelsValue
    proposal: PaymentChannelsProposal | None


class PublicHolidaysProposal(_Proposal):
    value: Holidays


class PublicHolidaysSetting(_Setting):
    key: Literal["public_holidays"]
    value: Holidays
    proposal: PublicHolidaysProposal | None


class BankPayoutCutoffProposal(_Proposal):
    value: ClockTime


class BankPayoutCutoffSetting(_Setting):
    key: Literal["bank_payout_cutoff"]
    value: ClockTime
    proposal: BankPayoutCutoffProposal | None


class MaturityRemindersProposal(_Proposal):
    value: Reminders


class MaturityRemindersSetting(_Setting):
    key: Literal["maturity_reminders"]
    value: Reminders
    proposal: MaturityRemindersProposal | None


class StaffIdleMinutesProposal(_Proposal):
    value: IdleMinutes


class StaffIdleMinutesSetting(_Setting):
    key: Literal["staff_idle_minutes"]
    value: IdleMinutes
    proposal: StaffIdleMinutesProposal | None


class OfficeNetworksProposal(_Proposal):
    value: Networks


class OfficeNetworksSetting(_Setting):
    key: Literal["office_networks"]
    value: Networks
    proposal: OfficeNetworksProposal | None


class StatementLayoutsProposal(_Proposal):
    value: Layouts


class StatementLayoutsSetting(_Setting):
    key: Literal["statement_layouts"]
    value: Layouts
    proposal: StatementLayoutsProposal | None


SettingView = Annotated[
    PaymentChannelsSetting
    | PublicHolidaysSetting
    | BankPayoutCutoffSetting
    | MaturityRemindersSetting
    | StaffIdleMinutesSetting
    | OfficeNetworksSetting
    | StatementLayoutsSetting,
    Field(discriminator="key"),
]
ProposalView = Annotated[
    PaymentChannelsProposal
    | PublicHolidaysProposal
    | BankPayoutCutoffProposal
    | MaturityRemindersProposal
    | StaffIdleMinutesProposal
    | OfficeNetworksProposal
    | StatementLayoutsProposal,
    Field(union_mode="left_to_right"),
]
_SETTING = TypeAdapter[SettingView](SettingView)
_PROPOSALS: dict[Key, type[_Proposal]] = {
    "payment_channels": PaymentChannelsProposal,
    "public_holidays": PublicHolidaysProposal,
    "bank_payout_cutoff": BankPayoutCutoffProposal,
    "maturity_reminders": MaturityRemindersProposal,
    "staff_idle_minutes": StaffIdleMinutesProposal,
    "office_networks": OfficeNetworksProposal,
    "statement_layouts": StatementLayoutsProposal,
}


class SettingsList(Contract):
    settings: list[SettingView]
    # The network address this request came from, as the office networks see it. Shown so a
    # member of staff can add the network they are on. None if it is not known.
    your_address: str | None
    # How a statement layout may write its dates.
    date_formats: list[DateFormat]


class SettingProposalOut(Contract):
    key: Key
    proposal: ProposalView


def _may_change(staff: StaffSession, key: Key) -> bool:
    return PERMISSIONS[SECTIONS[key]] in staff.permissions


def _proposal_data(proposal: service.Proposal, staff: StaffSession) -> dict[str, object]:
    return {
        "id": proposal.id,
        "value": DEFINITIONS[proposal.key].dump(proposal.value),
        "reason": proposal.reason,
        "proposed_by": proposal.proposed_by,
        "proposed_at": proposal.proposed_at,
        "yours": proposal.proposed_by == staff.staff_id,
    }


def _proposal_out(proposal: service.Proposal, staff: StaffSession) -> SettingProposalOut:
    shape = _PROPOSALS[proposal.key]
    return SettingProposalOut.model_validate(
        {"key": proposal.key, "proposal": shape.model_validate(_proposal_data(proposal, staff))}
    )


def _setting(setting: service.Setting, staff: StaffSession) -> SettingView:
    return _SETTING.validate_python(
        {
            "key": setting.key,
            "section": SECTIONS[setting.key],
            "value": DEFINITIONS[setting.key].dump(setting.value),
            "changed_at": setting.changed_at,
            "changed_by": setting.changed_by,
            "can_change": _may_change(staff, setting.key),
            "proposal": _proposal_data(setting.proposal, staff) if setting.proposal else None,
        }
    )


@staff_router.get("/settings/platform")
async def list_platform_settings(
    request: Request, staff: SettingsStaff, database: Db, clock: Time
) -> SettingsList:
    """Every platform setting: the value in use, and any change waiting for a decision."""
    async with database.actor_transaction(staff.actor) as session:
        listed = await service.list_settings(session, clock.now())
    return SettingsList(
        settings=[_setting(setting, staff) for setting in listed],
        your_address=caller_address(request),
        date_formats=list(DATE_FORMATS),  # type: ignore[arg-type]
    )


@staff_router.post("/settings/platform/proposals", status_code=201)
async def propose_platform_setting(
    request: Request,
    body: ProposeSetting,
    staff: SettingsStaff,
    database: Db,
    clock: Time,
) -> SettingProposalOut:
    """Put a new value forward. A different member of staff must approve it.

    403 without the section's permission. 409 if a change to this setting is already waiting,
    or, for the office networks, `leaves_you_out` when the list leaves out the network this
    request came from.
    """
    if not _may_change(staff, body.key):
        raise ApplicationError("forbidden")
    value = body.model_dump(mode="json")["value"]
    async with database.actor_transaction(staff.actor) as session:
        proposal = await service.propose(
            session,
            staff.actor,
            body.key,
            value,
            body.reason,
            clock,
            check=service.ApprovalCheck(caller_address(request)),
        )
    return _proposal_out(proposal, staff)


@staff_router.post("/settings/platform/proposals/{proposal_id}/decision")
async def decide_platform_setting(
    request: Request,
    proposal_id: PushId,
    body: DecideSetting,
    staff: SettingsDecider,
    database: Db,
    clock: Time,
) -> SettingProposalOut:
    """Approve or reject another person's proposal, or withdraw your own.

    Deciding needs the section's permission and a passkey or app code from the last few
    minutes (`confirm_it_is_you`), asked for before the proposal is looked up. An approved
    value is in use at once. 409 `leaves_you_out` when approving office networks that leave
    out the network this request came from.
    """
    async with database.actor_transaction(staff.actor) as session:
        key = await service.proposal_key(session, proposal_id)
        if key is None:
            raise ApplicationError("not_found")
        if not _may_change(staff, key):
            raise ApplicationError("forbidden")
        proposal = await service.decide(
            session,
            staff.actor,
            proposal_id,
            body.decision,
            body.reason,
            clock,
            check=service.ApprovalCheck(caller_address(request)),
        )
    return _proposal_out(proposal, staff)
