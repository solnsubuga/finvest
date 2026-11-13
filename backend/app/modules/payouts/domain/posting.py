"""The journal entries a payout causes. Pure: no I/O.

Four entries exist, and a payout has exactly two of them in its life: the reserve when it is
created, and one of settle, park or release when it finishes. Each has a fixed idempotency key
made from the payout's ID, so posting the same step twice posts once.

| Entry   | Debit                    | Credit                                   |
| ------- | ------------------------ | ---------------------------------------- |
| reserve | the handover account     | the client's payout in flight            |
| settle  | payout in flight         | the wallet or bank account it left from  |
| park    | payout in flight         | the client's payout pending              |
| release | payout in flight         | the handover account                     |

Every account but the wallet or bank is a liability to the same client, so until the provider
confirms, the total owed to the client does not change. Only settle reduces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    credit,
    debit,
)
from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.payouts import LedgerEffect
from app.shared.money import Currency, Money

IN_FLIGHT = "client.payout_in_flight"
PENDING = "client.payout_pending"
IOTEC_WALLET = "iotec.wallet"
BANK_TRUST = "bank.trust"


@dataclass(frozen=True, slots=True)
class PayoutTerms:
    payout_id: str
    client_id: str
    amount: Money
    source_account_id: str
    in_flight_account_id: str


def in_flight_account(payout_id: str, client_id: str, currency: Currency) -> AccountSpec:
    """One account per payout, so a payout's money can never be confused with another's."""
    return AccountSpec(
        IN_FLIGHT,
        AccountKind.LIABILITY,
        currency,
        "Payout in flight",
        owner_client_id=client_id,
        reference=payout_id,
    )


def funding_account(client_id: str, reference: str, currency: Currency) -> AccountSpec:
    """The only kind of account a payout may be funded from: money handed over for paying out.

    It is the client's "payout pending" account for one piece of business, such as the
    investment that matured, and `reference` names that business. A payout can never be
    funded from another payout's in-flight account, from the pot of failed payouts (which has
    no reference and is released by staff), or from any account of another client, because
    none of those can be named here.
    """
    if not reference:
        raise ValueError("A payout is funded from a named handover, never the failed-payout pot")
    return AccountSpec(
        PENDING,
        AccountKind.LIABILITY,
        currency,
        "Payout pending",
        owner_client_id=client_id,
        reference=reference,
    )


def pending_account(client_id: str, currency: Currency) -> AccountSpec:
    """Where a failed payout waits for staff. Not spendable, and not shown as a balance."""
    return AccountSpec(
        PENDING, AccountKind.LIABILITY, currency, "Payout pending", owner_client_id=client_id
    )


def paid_from_account(
    kind: DestinationKind, network: str | None, currency: Currency
) -> AccountSpec:
    """The pot the provider takes the money from: the ioTec wallet or the trust bank account."""
    if kind is DestinationKind.MOBILE_MONEY:
        if network is None:
            raise ValueError("A mobile money payout names its network")
        return AccountSpec(
            IOTEC_WALLET, AccountKind.ASSET, currency, "ioTec wallet", reference=network
        )
    return AccountSpec(BANK_TRUST, AccountKind.ASSET, currency, "Trust bank account")


def reserve(terms: PayoutTerms, effective_date: date) -> EntryDraft:
    return _entry(
        terms, "reserve", effective_date, terms.source_account_id, terms.in_flight_account_id
    )


def finish(
    terms: PayoutTerms, effect: LedgerEffect, effective_date: date, *, to_account_id: str
) -> EntryDraft:
    """The entry for the step into a final status. `to_account_id` is where the money goes:
    the paid-from pot for settle, the pending account for park, the source for release."""
    if effect is LedgerEffect.NONE:
        raise ValueError("This step moves no money")
    if effect is LedgerEffect.RELEASE and to_account_id != terms.source_account_id:
        raise ValueError("A released payout goes back where it came from")
    return _entry(terms, effect.value, effective_date, terms.in_flight_account_id, to_account_id)


def _entry(
    terms: PayoutTerms, step: str, effective_date: date, from_id: str, to_id: str
) -> EntryDraft:
    return EntryDraft(
        entry_type=f"payout.{step}",
        effective_date=effective_date,
        lines=(debit(from_id, terms.amount), credit(to_id, terms.amount)),
        idempotency_key=f"payout:{terms.payout_id}:{step}",
        source_type="payout",
        source_id=terms.payout_id,
    )
