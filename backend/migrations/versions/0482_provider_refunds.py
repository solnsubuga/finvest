"""Pay a card deposit back to the card that paid it, by two people.

Revision ID: 0482_provider_refunds
Revises: 0481_pesapal_pot

A client paid for an investment by card and should have the money back before the investment
starts. While the whole amount still waits in the client's `client.awaiting_start` account for
the investment, the money goes back through the provider that collected it, to the same card.
The card charge goes back too only when the refund is Finvest's mistake (the owner's decision,
2026-11-15).

One member of staff proposes, with a reason and whether it is Finvest's mistake. A different
one approves, sends it back with a note, or the proposer withdraws it. The worker carries out
what was approved, in two steps around the provider's call, which happens with no transaction
open:

1. It checks again and holds the money in one entry, then marks the refund `sending`:

       debit   client.awaiting_start            the client, the investment   the amount
       debit   payments.card_charges            when Finvest erred           the charge
       credit  payments.provider_refunds_due    the refund                   the total

2. It asks the provider once. A yes makes it `requested`: the provider's own people still pay
   it out. A no releases the hold with an entry that reverses it exactly, and the refund
   `failed`. A lost answer makes it `uncertain`, and it is never asked again: a provider takes
   one refund per payment.

A requested refund is `done` when a status read shows the payment reversed, or staff see the
refund on the provider's statement and say so. Staff also say what they found for an uncertain
one. Either way the worker posts the entry, so no staff transaction moves money:

       debit   payments.provider_refunds_due    the total
       credit  the provider's balance           the total

and the deposit's `provider_refund_id` is set in the same transaction. It stays `confirmed`,
because it was, but no longer counts as the investment's payment: the client can pay again, or
the investment is cancelled as unpaid at its pay-by time.

What the database enforces:

* A refund starts as proposed, by the staff member signed in, for a confirmed card deposit for
  an investment, not reversed or refunded, whose entry is the one that confirmed it, with the
  deposit's own client, investment, currency, amount and provider, and its card order's charge.
  The total is the amount, plus the charge only when Finvest erred.
* One live refund per deposit: proposed, approved, on its way or done.
* Nobody decides their own proposal, and only the proposer withdraws it. Nobody says what they
  found at the provider for a refund they proposed, and it is said once (an uncertain refund
  found requested may later be found paid or not paid).
* Only the worker moves an approved refund on, and each step fills in only its own columns.
  Done needs its own entry; a failure after the hold needs the entry that released it.
* A deposit's `provider_refund_id` is set once, by the worker, to a done refund of that deposit.
* Nothing is deleted, and a finished refund never changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0482_provider_refunds"
down_revision: str | None = "0481_pesapal_pot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"
GUARD_REVISION = "0022_bank_transfer_deposits"
REFUNDING_PROVIDERS = "'pesapal', 'card_sandbox'"
CODE = "^[a-z][a-z0-9_]{0,63}$"

TABLE = f"""
CREATE TABLE payment_provider_refunds (
    id push_id NOT NULL,
    deposit_id push_id NOT NULL,
    -- The deposit's client and investment. IDs in the identity and investments modules: no
    -- foreign key, a module owns only its own tables.
    client_id push_id NOT NULL,
    investment_id push_id NOT NULL,
    provider varchar(32) NOT NULL,
    currency varchar(3) NOT NULL,
    -- The investment's amount, the card charge the client paid on top, and what goes back.
    amount_minor bigint NOT NULL,
    charge_minor bigint NOT NULL,
    finvest_erred boolean NOT NULL,
    total_minor bigint NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    -- The entry that held the money, when the request was about to go.
    hold_entry_id push_id,
    sent_at timestamptz,
    -- What a member of staff found at the provider, for the worker to carry out.
    settlement varchar(10),
    settled_by push_id,
    settled_at timestamptz,
    settlement_note varchar(500),
    -- The entry that paid it out from the provider's balance, or released the hold.
    done_entry_id push_id,
    release_entry_id push_id,
    done_at timestamptz,
    failure_code varchar(64),
    CONSTRAINT pk_payment_provider_refunds PRIMARY KEY (id),
    CONSTRAINT fk_payment_provider_refunds_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT uq_payment_provider_refunds_hold_entry_id UNIQUE (hold_entry_id),
    CONSTRAINT uq_payment_provider_refunds_done_entry_id UNIQUE (done_entry_id),
    CONSTRAINT uq_payment_provider_refunds_release_entry_id UNIQUE (release_entry_id),
    CONSTRAINT ck_payment_provider_refunds_provider CHECK (provider IN ({REFUNDING_PROVIDERS})),
    CONSTRAINT ck_payment_provider_refunds_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_payment_provider_refunds_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_payment_provider_refunds_charge CHECK (charge_minor >= 0),
    CONSTRAINT ck_payment_provider_refunds_total CHECK (
        total_minor = amount_minor + CASE WHEN finvest_erred THEN charge_minor ELSE 0 END),
    CONSTRAINT ck_payment_provider_refunds_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payment_provider_refunds_status CHECK (status IN ('proposed', 'approved',
        'sent_back', 'withdrawn', 'sending', 'requested', 'uncertain', 'done', 'failed')),
    CONSTRAINT ck_payment_provider_refunds_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    CONSTRAINT ck_payment_provider_refunds_second_person
        CHECK (status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_payment_provider_refunds_withdrawn
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payment_provider_refunds_note
        CHECK (status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    -- The money is held from `sending` on, and released only by a failure after that.
    CONSTRAINT ck_payment_provider_refunds_held CHECK (
        (hold_entry_id IS NOT NULL) = (sent_at IS NOT NULL)
        AND (status NOT IN ('sending', 'requested', 'uncertain', 'done')
            OR hold_entry_id IS NOT NULL)
        AND (status IN ('sending', 'requested', 'uncertain', 'done', 'failed')
            OR hold_entry_id IS NULL)),
    CONSTRAINT ck_payment_provider_refunds_settlement CHECK (
        (settlement IS NULL) = (settled_by IS NULL)
        AND (settlement IS NULL) = (settled_at IS NULL)
        AND (settlement IS NULL OR settlement IN ('requested', 'paid', 'not_paid'))
        AND (settlement IS NULL OR btrim(coalesce(settlement_note, '')) <> '')
        AND (settled_by IS NULL OR settled_by <> proposed_by)),
    CONSTRAINT ck_payment_provider_refunds_outcome CHECK (
        (status IN ('done', 'failed')) = (done_at IS NOT NULL)
        AND (status = 'done') = (done_entry_id IS NOT NULL)
        AND (status = 'failed') = (failure_code IS NOT NULL)
        AND (release_entry_id IS NULL OR status = 'failed')
        AND (status <> 'failed' OR (hold_entry_id IS NULL) = (release_entry_id IS NULL))),
    CONSTRAINT ck_payment_provider_refunds_failure_code
        CHECK (failure_code IS NULL OR failure_code ~ '{CODE}')
)
"""
INDEXES = [
    # A deposit is paid back once, and is in at most one live refund at a time.
    "CREATE UNIQUE INDEX uq_payment_provider_refunds_deposit ON payment_provider_refunds "
    "(deposit_id) WHERE status NOT IN ('sent_back', 'withdrawn', 'failed')",
    "CREATE INDEX ix_payment_provider_refunds_status ON payment_provider_refunds "
    "(status, proposed_at)",
]

# Columns each step may fill in. Anything else that differs is refused.
DECIDE = "{status,decided_by,decided_at,decision_note}"
HOLD = "{status,hold_entry_id,sent_at}"
SETTLE = "{settlement,settled_by,settled_at,settlement_note}"
FINISH = "{status,done_entry_id,release_entry_id,done_at,failure_code}"

# Runs as the caller, so the worker is told apart by its role. Staff and the worker both read
# every deposit, card order and ledger entry. It pins its search path and names tables with
# their schema.
REFUNDS_GUARD = f"""
CREATE FUNCTION finvest_payment_provider_refunds_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        deposit public.payment_deposits%ROWTYPE;
        order_charge bigint;
        entry public.ledger_journal_entries%ROWTYPE;
        is_worker boolean := public.finvest_actor_kind() = 'system'
            AND current_user = '{WORKER_ROLE}';
        is_staff boolean := public.finvest_actor_kind() = 'staff';
        moved boolean := NEW.status <> OLD.status;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            SELECT * INTO deposit FROM public.payment_deposits WHERE id = NEW.deposit_id;
            SELECT charge_minor INTO order_charge FROM public.payment_card_orders
             WHERE deposit_id = NEW.deposit_id AND confirmation_code IS NOT NULL;
            IF NOT is_staff
                OR NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id()
                OR NEW.decided_by IS NOT NULL OR NEW.hold_entry_id IS NOT NULL
                OR NEW.settlement IS NOT NULL OR NEW.done_at IS NOT NULL
                OR NEW.failure_code IS NOT NULL
                OR deposit.id IS NULL OR deposit.status <> 'confirmed'
                OR deposit.method <> 'card' OR deposit.provider <> NEW.provider
                OR deposit.reversal_id IS NOT NULL OR deposit.provider_refund_id IS NOT NULL
                OR deposit.purpose_type <> 'investment'
                OR deposit.client_id <> NEW.client_id OR deposit.purpose_id <> NEW.investment_id
                OR deposit.currency <> NEW.currency OR deposit.amount_minor <> NEW.amount_minor
                OR order_charge IS DISTINCT FROM NEW.charge_minor
                OR NOT EXISTS (
                    SELECT FROM public.ledger_journal_entries AS confirmed
                    WHERE confirmed.id = deposit.ledger_entry_id
                    AND confirmed.entry_type = 'payment.deposit_confirmed'
                    AND confirmed.source_type = 'payment.deposit'
                    AND confirmed.source_id = deposit.id)
            THEN
                RAISE EXCEPTION 'a refund starts as proposed, by the person signed in, for a '
                    'confirmed card deposit for an investment that was not taken back'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('sent_back', 'withdrawn', 'done', 'failed') THEN
            RAISE EXCEPTION 'a finished refund never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'proposed' THEN
            IF NOT is_staff
                OR NEW.status NOT IN ('approved', 'sent_back', 'withdrawn')
                OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
                OR (to_jsonb(NEW) - '{DECIDE}'::text[]) <> (to_jsonb(OLD) - '{DECIDE}'::text[])
            THEN
                RAISE EXCEPTION 'a refund is only decided, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        -- Staff say what they found at the provider, once, and nothing else.
        IF is_staff THEN
            -- An uncertain refund found requested can still be found paid or not paid.
            IF OLD.status NOT IN ('requested', 'uncertain')
                OR (OLD.settlement IS NOT NULL
                    AND NOT (OLD.status = 'requested' AND OLD.settlement = 'requested'))
                OR NEW.settled_by IS DISTINCT FROM public.finvest_actor_id()
                OR (OLD.status = 'requested' AND NEW.settlement NOT IN ('paid', 'not_paid'))
                OR (to_jsonb(NEW) - '{SETTLE}'::text[]) <> (to_jsonb(OLD) - '{SETTLE}'::text[])
            THEN
                RAISE EXCEPTION 'staff only say what they found at the provider, once'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NOT is_worker THEN
            RAISE EXCEPTION 'only the worker moves an approved refund on'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'approved' AND NEW.status = 'sending' THEN
            SELECT * INTO entry FROM public.ledger_journal_entries WHERE id = NEW.hold_entry_id;
            IF (to_jsonb(NEW) - '{HOLD}'::text[]) <> (to_jsonb(OLD) - '{HOLD}'::text[])
                OR entry.id IS NULL OR entry.entry_type <> 'payment.provider_refund_held'
                OR entry.source_type <> 'payment.provider_refund' OR entry.source_id <> NEW.id
            THEN
                RAISE EXCEPTION 'a refund is sent only with its own hold'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (to_jsonb(NEW) - '{FINISH}'::text[]) <> (to_jsonb(OLD) - '{FINISH}'::text[])
            OR NOT moved
            OR (OLD.status, NEW.status) NOT IN (('approved', 'failed'), ('sending', 'requested'),
                ('sending', 'uncertain'), ('sending', 'failed'), ('requested', 'done'),
                ('requested', 'failed'), ('uncertain', 'requested'), ('uncertain', 'done'),
                ('uncertain', 'failed'))
            OR (NEW.status IN ('requested', 'uncertain')
                AND (NEW.done_entry_id IS NOT NULL OR NEW.release_entry_id IS NOT NULL
                    OR NEW.done_at IS NOT NULL OR NEW.failure_code IS NOT NULL))
            -- What staff found decides an uncertain refund, and a requested one that fails.
            OR (OLD.status = 'uncertain' AND NEW.status <> CASE OLD.settlement
                WHEN 'requested' THEN 'requested' WHEN 'paid' THEN 'done'
                WHEN 'not_paid' THEN 'failed' END)
            OR (OLD.status = 'requested' AND NEW.status = 'failed'
                AND OLD.settlement IS DISTINCT FROM 'not_paid')
        THEN
            RAISE EXCEPTION 'a refund cannot move from % to % like this', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'done' THEN
            SELECT * INTO entry FROM public.ledger_journal_entries WHERE id = NEW.done_entry_id;
            IF entry.id IS NULL OR entry.entry_type <> 'payment.provider_refund_paid'
                OR entry.source_type <> 'payment.provider_refund' OR entry.source_id <> NEW.id
            THEN
                RAISE EXCEPTION 'a refund is done only by its own entry' USING ERRCODE = '42501';
            END IF;
        END IF;
        IF NEW.release_entry_id IS NOT NULL THEN
            SELECT * INTO entry FROM public.ledger_journal_entries
             WHERE id = NEW.release_entry_id;
            IF entry.id IS NULL OR entry.reverses_entry_id IS DISTINCT FROM NEW.hold_entry_id
                OR entry.source_type <> 'payment.provider_refund' OR entry.source_id <> NEW.id
            THEN
                RAISE EXCEPTION 'a hold is released only by reversing it exactly'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name and column lists


def deposit_guard(moves: Sequence[tuple[str, str]]) -> str:
    """The deposit update guard of 0261, which also lets the worker mark a deposit refunded."""
    moves_sql = ", ".join(f"('{old}', '{new}')" for old, new in moves)
    return f"""
CREATE OR REPLACE FUNCTION finvest_payment_deposit_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF (NEW.id, NEW.client_id, NEW.purpose_type, NEW.purpose_id, NEW.method, NEW.provider,
            NEW.currency, NEW.amount_minor, NEW.payer_msisdn, NEW.created_at)
           IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.purpose_type, OLD.purpose_id, OLD.method, OLD.provider,
            OLD.currency, OLD.amount_minor, OLD.payer_msisdn, OLD.created_at) THEN
            RAISE EXCEPTION 'a deposit''s terms cannot change' USING ERRCODE = '42501';
        END IF;
        IF NEW.reversal_id IS DISTINCT FROM OLD.reversal_id THEN
            -- Set once, on a confirmed deposit, to a done reversal of it, and nothing else.
            IF OLD.reversal_id IS NOT NULL OR OLD.status <> 'confirmed'
                OR OLD.provider_refund_id IS NOT NULL
                OR (to_jsonb(NEW) - 'reversal_id') <> (to_jsonb(OLD) - 'reversal_id')
                OR NOT EXISTS (
                    SELECT FROM public.payment_deposit_reversals AS reversal
                    WHERE reversal.id = NEW.reversal_id AND reversal.deposit_id = NEW.id
                    AND reversal.status = 'done')
            THEN
                RAISE EXCEPTION 'a deposit is marked reversed once, by its done reversal'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.provider_refund_id IS DISTINCT FROM OLD.provider_refund_id THEN
            -- Set once, on a confirmed deposit, to a done refund of it, and nothing else.
            IF OLD.provider_refund_id IS NOT NULL OR OLD.status <> 'confirmed'
                OR OLD.reversal_id IS NOT NULL
                OR (to_jsonb(NEW) - 'provider_refund_id')
                    <> (to_jsonb(OLD) - 'provider_refund_id')
                OR NOT EXISTS (
                    SELECT FROM public.payment_provider_refunds AS refund
                    WHERE refund.id = NEW.provider_refund_id AND refund.deposit_id = NEW.id
                    AND refund.status = 'done')
            THEN
                RAISE EXCEPTION 'a deposit is marked refunded once, by its done refund'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('confirmed', 'failed') THEN
            RAISE EXCEPTION 'a % deposit is final', OLD.status USING ERRCODE = '42501';
        END IF;
        IF NEW.status <> OLD.status
           AND (OLD.status, NEW.status) NOT IN ({moves_sql}) THEN
            RAISE EXCEPTION 'a deposit cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        IF OLD.provider_reference IS NOT NULL
           AND NEW.provider_reference IS DISTINCT FROM OLD.provider_reference THEN
            RAISE EXCEPTION 'a provider reference cannot change' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant status pairs


COLUMNS = [
    # The done refund that paid this deposit back to the card. Set once, by the worker.
    "ALTER TABLE payment_deposits ADD COLUMN provider_refund_id push_id",
    "ALTER TABLE payment_deposits ADD CONSTRAINT uq_payment_deposits_provider_refund_id "
    "UNIQUE (provider_refund_id)",
    # A refunded deposit no longer holds the investment's one live payment.
    "DROP INDEX uq_payment_deposits_live_investment",
    "CREATE UNIQUE INDEX uq_payment_deposits_live_investment ON payment_deposits (purpose_id) "
    "WHERE purpose_type = 'investment' AND status <> 'failed' AND reversal_id IS NULL "
    "AND provider_refund_id IS NULL",
]

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payment_provider_refunds_guard() FROM PUBLIC",
    "CREATE TRIGGER payment_provider_refunds_guard BEFORE INSERT OR UPDATE "
    "ON payment_provider_refunds FOR EACH ROW "
    "EXECUTE FUNCTION finvest_payment_provider_refunds_guard()",
    "CREATE TRIGGER payment_provider_refunds_no_delete BEFORE DELETE "
    "ON payment_provider_refunds FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
    "CREATE TRIGGER payment_provider_refunds_no_truncate BEFORE TRUNCATE "
    "ON payment_provider_refunds FOR EACH STATEMENT "
    "EXECUTE FUNCTION finvest_payments_immutable()",
    "REVOKE ALL ON payment_provider_refunds FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, settlement, "
    f"settled_by, settled_at, settlement_note) ON payment_provider_refunds TO {APP_ROLE}",
    "GRANT SELECT, UPDATE (status, hold_entry_id, sent_at, done_entry_id, release_entry_id, "
    f"done_at, failure_code) ON payment_provider_refunds TO {WORKER_ROLE}",
    f"GRANT UPDATE (provider_refund_id) ON payment_deposits TO {WORKER_ROLE}",
    # Staff only on the API's role: a client transaction reads no refund.
    "ALTER TABLE payment_provider_refunds ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY payment_provider_refunds_app_read ON payment_provider_refunds FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF})",
    "CREATE POLICY payment_provider_refunds_app_insert ON payment_provider_refunds "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    "CREATE POLICY payment_provider_refunds_app_update ON payment_provider_refunds "
    f"FOR UPDATE TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    "CREATE POLICY payment_provider_refunds_worker ON payment_provider_refunds "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


def _guard_moves() -> Sequence[tuple[str, str]]:
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to rebuild the deposit guard")
    moves: Sequence[tuple[str, str]] = script.get_revision(GUARD_REVISION).module.ALLOWED_MOVES
    return moves


def upgrade() -> None:
    # The column first: the refunds guard reads it.
    for statement in [*COLUMNS, TABLE, *INDEXES, REFUNDS_GUARD, *RULES]:
        op.execute(statement)
    op.execute(deposit_guard(_guard_moves()))


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_provider_refunds)
        OR EXISTS (SELECT FROM payment_deposits WHERE provider_refund_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the record of refunded deposits';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A refund is part of the story of the books; a downgrade never erases one.
    op.execute(EMPTY_GUARD)
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to restore the deposit guard")
    earlier = script.get_revision("0261_payment_deposit_reversals").module
    op.execute(earlier.deposit_guard(_guard_moves()))
    for statement in (
        f"REVOKE UPDATE (provider_refund_id) ON payment_deposits FROM {WORKER_ROLE}",
        "DROP TABLE payment_provider_refunds",
        "DROP FUNCTION finvest_payment_provider_refunds_guard()",
        "DROP INDEX uq_payment_deposits_live_investment",
        "CREATE UNIQUE INDEX uq_payment_deposits_live_investment ON payment_deposits "
        "(purpose_id) WHERE purpose_type = 'investment' AND status <> 'failed' "
        "AND reversal_id IS NULL",
        "ALTER TABLE payment_deposits DROP COLUMN provider_refund_id",
    ):
        op.execute(statement)
