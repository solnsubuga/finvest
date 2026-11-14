"""Send part or all of a running investment to another client.

Revision ID: 0241_investment_transfers
Revises: 0240_kyc_transfer_recipients

`investment_transfers` keeps each request. The client asks; a large one waits for two members
of staff; the worker then closes the investment as `transferred` and opens two new ones in the
same product, rate, tax and maturity date: the recipient's for exactly what was sent, and the
sender's for the rest. One ledger entry moves the money from the old holding to the new ones.
Nothing leaves Finvest and nothing is cashed out.

What the database enforces:

* A transfer starts as asked by the client signed in, from their own running investment that
  has no early withdrawal asked for, to another client whose identity check is approved, now. One open transfer per
  investment.
* Only staff propose letting a held one through, a different member of staff approves, and only
  the proposer withdraws the proposal. Declining is one person's step, as for a held payout.
  Only the sender cancels, and only while it is held.
* Only the worker records what happened, once.
* The investments guard of migration 0066 is replaced: only the worker opens a transfer's
  investments, with the terms and amounts above, and closes the old one, only while its
  transfer waits; a transferred investment never changes; and an early withdrawal cannot be
  asked for while a transfer of the investment is open.

See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0241_investment_transfers"
down_revision: str | None = "0240_kyc_transfer_recipients"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"
CLIENT = "public.finvest_actor_kind() = 'client'"

TABLE = """
CREATE TABLE investment_transfers (
    id push_id NOT NULL,
    -- The running investment it is sent from. It closes as `transferred` when this is done.
    investment_id push_id NOT NULL,
    -- Client IDs in the identity module. No foreign key: a module owns only its own tables.
    sender_client_id push_id NOT NULL,
    recipient_client_id push_id NOT NULL,
    -- How each is shown to the other: the first given name and the surname's initial, from
    -- their approved identity checks when the transfer was asked for.
    sender_name varchar(80) NOT NULL,
    recipient_name varchar(80) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    -- What the sender asked to send. `whole` sends the whole value at the moment it is done,
    -- including interest earned after the request.
    amount_minor bigint NOT NULL,
    whole boolean NOT NULL,
    -- A few words for the recipient, from the sender.
    message varchar(140),
    status varchar(16) NOT NULL,
    -- Whether it was held for staff when it was asked for, and why: `size` (large) or
    -- `screening` (the sender was on a screening hold). The client is never told which.
    held boolean NOT NULL,
    held_for varchar(10),
    requested_at timestamptz NOT NULL,
    -- A held transfer: the first person's proposal to let it through, the second person who
    -- approved it, or the one person who declined it. Staff IDs in the identity module.
    release_proposed_by push_id,
    release_proposed_at timestamptz,
    release_reason varchar(500),
    released_by push_id,
    released_at timestamptz,
    declined_by push_id,
    declined_at timestamptz,
    decision_reason varchar(500),
    cancelled_at timestamptz,
    -- What the worker did: the amount the recipient got, the two investments and the entry.
    finished_at timestamptz,
    sent_minor bigint,
    received_investment_id push_id,
    kept_investment_id push_id,
    entry_id push_id,
    failure_code varchar(64),
    CONSTRAINT pk_investment_transfers PRIMARY KEY (id),
    CONSTRAINT fk_investment_transfers_investment
        FOREIGN KEY (investment_id) REFERENCES investments (id),
    CONSTRAINT fk_investment_transfers_received
        FOREIGN KEY (received_investment_id) REFERENCES investments (id),
    CONSTRAINT fk_investment_transfers_kept
        FOREIGN KEY (kept_investment_id) REFERENCES investments (id),
    CONSTRAINT uq_investment_transfers_entry UNIQUE (entry_id),
    CONSTRAINT ck_investment_transfers_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_investment_transfers_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_investment_transfers_someone_else
        CHECK (recipient_client_id <> sender_client_id),
    CONSTRAINT ck_investment_transfers_names CHECK (
        btrim(sender_name) <> '' AND btrim(recipient_name) <> ''),
    CONSTRAINT ck_investment_transfers_message CHECK (
        message IS NULL OR btrim(message) <> ''),
    CONSTRAINT ck_investment_transfers_status CHECK (status IN (
        'waiting', 'held', 'release_proposed', 'done', 'failed', 'declined', 'cancelled')),
    CONSTRAINT ck_investment_transfers_proposal CHECK (
        (release_proposed_by IS NULL) = (release_proposed_at IS NULL)
        AND (release_proposed_by IS NULL) = (release_reason IS NULL)
        AND (status <> 'release_proposed' OR release_proposed_by IS NOT NULL)
        AND (status <> 'held' OR release_proposed_by IS NULL)
        AND (release_reason IS NULL OR btrim(release_reason) <> '')),
    -- Released only by a second person, after a first proposed it.
    CONSTRAINT ck_investment_transfers_released CHECK (
        (released_by IS NULL) = (released_at IS NULL)
        AND (released_by IS NULL OR (held AND release_proposed_by IS NOT NULL
            AND released_by <> release_proposed_by))),
    CONSTRAINT ck_investment_transfers_held_for CHECK (
        held = (held_for IS NOT NULL) AND (held_for IS NULL OR held_for IN ('size', 'screening'))),
    CONSTRAINT ck_investment_transfers_held CHECK (
        held OR (release_proposed_by IS NULL AND declined_by IS NULL AND cancelled_at IS NULL)),
    CONSTRAINT ck_investment_transfers_through CHECK (
        status NOT IN ('waiting', 'done', 'failed') OR NOT held OR released_by IS NOT NULL),
    CONSTRAINT ck_investment_transfers_declined CHECK (
        (status = 'declined') = (declined_by IS NOT NULL)
        AND (declined_by IS NULL) = (declined_at IS NULL)
        AND (status <> 'declined' OR btrim(coalesce(decision_reason, '')) <> '')),
    CONSTRAINT ck_investment_transfers_cancelled CHECK (
        (status = 'cancelled') = (cancelled_at IS NOT NULL)),
    -- Done means the money moved, to a new investment for the recipient. Failed means it did
    -- not.
    CONSTRAINT ck_investment_transfers_finished CHECK (
        (status IN ('done', 'failed')) = (finished_at IS NOT NULL)
        AND (status = 'done') = (entry_id IS NOT NULL)
        AND (status = 'done') = (received_investment_id IS NOT NULL)
        AND (status = 'done') = (sent_minor IS NOT NULL)
        AND (status = 'failed') = (failure_code IS NOT NULL)
        AND (kept_investment_id IS NULL OR status = 'done')
        AND (sent_minor IS NULL OR sent_minor > 0)
        AND (NOT whole OR kept_investment_id IS NULL)
        AND (whole OR sent_minor IS NULL OR sent_minor = amount_minor))
)
"""
INDEXES = [
    # One open transfer per investment.
    "CREATE UNIQUE INDEX uq_investment_transfers_open ON investment_transfers (investment_id) "
    "WHERE status IN ('waiting', 'held', 'release_proposed')",
    "CREATE INDEX ix_investment_transfers_sender ON investment_transfers "
    "(sender_client_id, requested_at DESC, id DESC)",
    "CREATE INDEX ix_investment_transfers_recipient ON investment_transfers "
    "(recipient_client_id, requested_at DESC, id DESC)",
    "CREATE INDEX ix_investment_transfers_held ON investment_transfers (requested_at, id) "
    "WHERE status IN ('held', 'release_proposed')",
]

TRANSFERS_GUARD = f"""
CREATE FUNCTION finvest_investment_transfers_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        who text := finvest_actor_id();
        is_worker boolean := finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}';
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF actor IS DISTINCT FROM 'client' OR NEW.sender_client_id IS DISTINCT FROM who
                OR NEW.status NOT IN ('waiting', 'held') OR NEW.held <> (NEW.status = 'held')
                OR abs(extract(epoch FROM NEW.requested_at - clock_timestamp())) > 300
                OR NOT EXISTS (SELECT FROM investments i WHERE i.id = NEW.investment_id
                    AND i.client_id = NEW.sender_client_id AND i.status = 'active'
                    AND i.withdrawal_requested_at IS NULL AND i.currency = NEW.currency)
                OR NOT EXISTS (SELECT FROM finvest_kyc_transfer_recipient(NULL,
                    NEW.recipient_client_id))
                OR num_nonnulls(NEW.release_proposed_by, NEW.released_by, NEW.declined_by,
                    NEW.cancelled_at, NEW.finished_at, NEW.entry_id) > 0
                -- A sender on a screening hold is always held for staff (migration 0181).
                OR (NOT NEW.held AND screening_payout_clearance(NEW.sender_client_id,
                    clock_timestamp()) IN ('wait', 'blocked'))
            THEN
                RAISE EXCEPTION 'a transfer is asked for by the client signed in, from their own running investment, now'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.id, NEW.investment_id, NEW.sender_client_id, NEW.recipient_client_id,
            NEW.sender_name, NEW.recipient_name, NEW.currency, NEW.amount_minor, NEW.whole,
            NEW.message, NEW.held, NEW.held_for, NEW.requested_at)
            IS DISTINCT FROM (OLD.id, OLD.investment_id, OLD.sender_client_id,
            OLD.recipient_client_id, OLD.sender_name, OLD.recipient_name, OLD.currency,
            OLD.amount_minor, OLD.whole, OLD.message, OLD.held, OLD.held_for,
            OLD.requested_at) THEN
            RAISE EXCEPTION 'what a transfer sends never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'held' AND NEW.status = 'release_proposed' THEN
            IF actor IS DISTINCT FROM 'staff' OR NEW.release_proposed_by IS DISTINCT FROM who THEN
                RAISE EXCEPTION 'a release is proposed by the member of staff signed in'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'release_proposed' AND NEW.status = 'held' THEN
            IF actor IS DISTINCT FROM 'staff' OR who IS DISTINCT FROM OLD.release_proposed_by THEN
                RAISE EXCEPTION 'only the proposer withdraws a release' USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'release_proposed' AND NEW.status = 'waiting' THEN
            IF actor IS DISTINCT FROM 'staff' OR NEW.released_by IS DISTINCT FROM who
                OR who = OLD.release_proposed_by
                OR NEW.release_proposed_by IS DISTINCT FROM OLD.release_proposed_by THEN
                RAISE EXCEPTION 'a release is approved by a second member of staff'
                    USING ERRCODE = '42501';
            END IF;
            IF screening_payout_clearance(NEW.sender_client_id, clock_timestamp())
                IN ('wait', 'blocked')
                OR screening_payout_clearance(NEW.recipient_client_id, clock_timestamp())
                IN ('wait', 'blocked') THEN
                RAISE EXCEPTION 'a transfer is not let through while either client is held by screening'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status IN ('held', 'release_proposed') AND NEW.status = 'declined' THEN
            IF actor IS DISTINCT FROM 'staff' OR NEW.declined_by IS DISTINCT FROM who THEN
                RAISE EXCEPTION 'a transfer is declined by the member of staff signed in'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status IN ('held', 'release_proposed') AND NEW.status = 'cancelled' THEN
            IF actor IS DISTINCT FROM 'client' OR who IS DISTINCT FROM OLD.sender_client_id THEN
                RAISE EXCEPTION 'only the sender cancels a transfer' USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'waiting' AND NEW.status IN ('done', 'failed') THEN
            IF NOT is_worker THEN
                RAISE EXCEPTION 'only the worker records what a transfer did'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.status = 'done' AND (
                screening_payout_clearance(NEW.sender_client_id, clock_timestamp())
                IN ('wait', 'blocked')
                OR screening_payout_clearance(NEW.recipient_client_id, clock_timestamp())
                IN ('wait', 'blocked')) THEN
                RAISE EXCEPTION 'a transfer is not made while either client is held by screening'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            RAISE EXCEPTION 'a transfer cannot go from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

COLUMNS = [
    "ALTER TABLE investments ADD COLUMN transferred_at timestamptz",
    # The ledger entry that moved the holding to the two new investments.
    "ALTER TABLE investments ADD COLUMN transfer_entry_id push_id",
    "ALTER TABLE investments DROP CONSTRAINT ck_investments_status",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_status CHECK (status IN "
    "('awaiting_payment', 'active', 'cancelled', 'matured', 'withdrawn', 'unwound', "
    "'transferred'))",
    "ALTER TABLE investments DROP CONSTRAINT ck_investments_started",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_started CHECK ("
    "(status IN ('active', 'matured', 'withdrawn', 'unwound', 'transferred')) "
    "= (start_date IS NOT NULL) "
    "AND (start_date IS NULL) = (started_at IS NULL) "
    "AND (start_date IS NULL) = (start_entry_id IS NULL))",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_transferred CHECK ("
    "(status = 'transferred') = (transferred_at IS NOT NULL) "
    "AND (status = 'transferred') = (transfer_entry_id IS NOT NULL))",
    "ALTER TABLE investments DROP CONSTRAINT ck_investments_source",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_source CHECK ("
    "(source_type IS NULL AND source_id IS NULL) OR "
    "(source_type IN ('autoinvest_run', 'goal_saving', 'transfer_received', 'transfer_kept') "
    "AND source_id IS NOT NULL))",
    # One transfer entry starts both new investments, the recipient's and the sender's, so
    # an entry starts one investment, or one of each side of a transfer.
    "ALTER TABLE investments DROP CONSTRAINT uq_investments_start_entry",
    "CREATE UNIQUE INDEX uq_investments_start_entry ON investments (start_entry_id) "
    "WHERE source_type IS DISTINCT FROM 'transfer_received' "
    "AND source_type IS DISTINCT FROM 'transfer_kept'",
    "CREATE UNIQUE INDEX uq_investments_transfer_start ON investments "
    "(start_entry_id, source_type) "
    "WHERE source_type IN ('transfer_received', 'transfer_kept')",
]

_FREE = (
    "status,at_maturity,start_date,maturity_date,started_at,start_entry_id,cancelled_at,"
    "accrued_through,accrued_gross_minor,accrued_tax_minor,updated_at,payout_destination_id,"
    "matured_at,maturity_entry_id,rolled_into_id,payout_id,payout_refusal,"
    "withdrawal_requested_at,withdrawal_destination_id,withdrawn_at,withdrawal_entry_id,"
    "withdrawal_payout_id,unwound_at,unwind_entry_id,transferred_at,transfer_entry_id"
)

# The whole guard again, as PostgreSQL replaces a function in one piece. What is new: the
# worker opens a transfer's two investments and closes the one it came from; a transferred
# investment never changes; no early withdrawal while a transfer is open.
GUARD = f"""
CREATE OR REPLACE FUNCTION finvest_investments_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        is_worker boolean := finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}';
        product investment_products%ROWTYPE;
        rate investment_rates%ROWTYPE;
        day date;
        transfer investment_transfers%ROWTYPE;
        source investments%ROWTYPE;
    BEGIN
        IF TG_OP = 'INSERT' AND NEW.legacy_reference IS NOT NULL THEN
            -- Carried over from Finvest 1.0 at the switch, by the worker only: already running,
            -- on the old system's own rate and tax, with the interest earned up to a day
            -- before today and before it matures. Nothing else of a new investment applies.
            SELECT * INTO product FROM investment_products WHERE id = NEW.product_id;
            day := (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date;
            IF NOT is_worker OR NEW.status <> 'active'
                OR product.id IS NULL OR product.status = 'draft'
                OR NEW.currency <> product.currency
                OR NEW.term_days IS DISTINCT FROM product.term_days
                OR NEW.rolled_from_id IS NOT NULL OR NEW.source_id IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL
                OR NEW.withdrawal_requested_at IS NOT NULL
                OR NEW.accrued_through IS NULL OR NEW.accrued_through >= day
                OR NEW.start_date > NEW.accrued_through
                OR (NEW.maturity_date IS NOT NULL AND NEW.accrued_through >= NEW.maturity_date)
            THEN
                RAISE EXCEPTION 'only the switch carries over a running investment'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' AND NEW.source_type IN ('transfer_received', 'transfer_kept') THEN
            -- One of the two investments a transfer opens, by the worker only, while the
            -- transfer waits and the investment it comes from still runs. Both take that
            -- investment's product, rate, tax and maturity date, starting today. The
            -- recipient's is exactly what was sent and rolls over unless they choose
            -- otherwise; the sender's is the rest and keeps the sender's choice.
            SELECT * INTO transfer FROM investment_transfers WHERE id = NEW.source_id;
            SELECT * INTO source FROM investments WHERE id = transfer.investment_id;
            day := (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date;
            IF NOT is_worker OR transfer.id IS NULL OR transfer.status <> 'waiting'
                OR source.id IS NULL OR source.status <> 'active'
                OR source.withdrawal_requested_at IS NOT NULL
                OR NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR NEW.rolled_from_id IS NOT NULL OR NEW.legacy_reference IS NOT NULL
                OR NEW.withdrawal_requested_at IS NOT NULL
                OR NEW.product_id <> source.product_id OR NEW.currency <> source.currency
                OR NEW.annual_rate_bps <> source.annual_rate_bps OR NEW.tax_bps <> source.tax_bps
                OR NEW.term_days IS DISTINCT FROM (source.maturity_date - day)
            THEN
                RAISE EXCEPTION 'only the worker opens the investments of a transfer'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.source_type = 'transfer_received' THEN
                IF NEW.client_id <> transfer.recipient_client_id
                    OR NEW.payout_destination_id IS NOT NULL
                    OR NEW.at_maturity IS DISTINCT FROM
                        (CASE WHEN source.term_days IS NULL THEN NULL ELSE 'roll_over' END)
                    OR NEW.principal_minor <> (CASE WHEN transfer.whole
                        THEN source.principal_minor + source.accrued_gross_minor
                            - source.accrued_tax_minor
                        ELSE transfer.amount_minor END)
                THEN
                    RAISE EXCEPTION 'the recipient gets exactly what was sent'
                        USING ERRCODE = '42501';
                END IF;
            ELSIF transfer.whole OR NEW.client_id <> transfer.sender_client_id
                OR NEW.at_maturity IS DISTINCT FROM source.at_maturity
                OR NEW.payout_destination_id IS DISTINCT FROM source.payout_destination_id
                OR NEW.principal_minor <> source.principal_minor + source.accrued_gross_minor
                    - source.accrued_tax_minor - transfer.amount_minor
            THEN
                RAISE EXCEPTION 'the sender keeps exactly what was not sent'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL THEN
                RAISE EXCEPTION 'an investment begins unpaid, with nothing earned'
                    USING ERRCODE = '42501';
            END IF;
            -- A client creates their own. Only the worker creates one, from a rollover or a
            -- saving source such as an automatic investing run.
            IF (NEW.rolled_from_id IS NOT NULL OR NEW.source_id IS NOT NULL) <> is_worker
                OR (NOT is_worker AND actor IS DISTINCT FROM 'client') THEN
                RAISE EXCEPTION 'an investment is created by its client, a rollover or a saving source'
                    USING ERRCODE = '42501';
            END IF;
            -- The terms are the catalogue's on the day of the review, not whatever was sent.
            SELECT * INTO product FROM investment_products WHERE id = NEW.product_id;
            day := (NEW.created_at AT TIME ZONE 'Africa/Kampala')::date;
            SELECT * INTO rate FROM investment_rates
                WHERE product_id = NEW.product_id AND effective_from <= day
                ORDER BY effective_from DESC LIMIT 1;
            IF product.id IS NULL OR rate.id IS NULL OR product.status <> 'live'
                OR NEW.currency <> product.currency
                OR NEW.principal_minor < product.minimum_minor
                OR NEW.term_days IS DISTINCT FROM product.term_days
                OR NEW.annual_rate_bps <> rate.annual_rate_bps
                OR NEW.tax_bps <> rate.tax_bps THEN
                RAISE EXCEPTION 'an investment takes the product''s own terms on the day'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.status = 'cancelled' THEN
            RAISE EXCEPTION 'a cancelled investment never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - '{{{_FREE}}}'::text[]) <> (to_jsonb(OLD) - '{{{_FREE}}}'::text[]) THEN
            RAISE EXCEPTION 'the terms of an investment never change' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'unwound' THEN
            RAISE EXCEPTION 'an undone investment never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'transferred' THEN
            RAISE EXCEPTION 'a transferred investment never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'withdrawn' THEN
            -- Closed. The one thing left is the payout that carries the money, recorded once
            -- by the worker when the dealing desk has funded it.
            IF (to_jsonb(NEW) - '{{withdrawal_payout_id,updated_at}}'::text[])
                <> (to_jsonb(OLD) - '{{withdrawal_payout_id,updated_at}}'::text[])
                OR OLD.withdrawal_payout_id IS NOT NULL OR NEW.withdrawal_payout_id IS NULL
                OR NOT is_worker THEN
                RAISE EXCEPTION 'a withdrawn investment never changes' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'matured' THEN
            -- Closed. The one thing left is sending on money that is waiting for a
            -- destination, by the client or the worker.
            IF (to_jsonb(NEW) - '{{payout_id,payout_refusal,updated_at}}'::text[])
                <> (to_jsonb(OLD) - '{{payout_id,payout_refusal,updated_at}}'::text[])
                OR OLD.payout_id IS NOT NULL OR OLD.rolled_into_id IS NOT NULL
                OR NEW.payout_id IS NULL OR NEW.payout_refusal IS NOT NULL
                OR NOT (is_worker OR actor = 'client') THEN
                RAISE EXCEPTION 'a matured investment never changes' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.status <> OLD.status THEN
            IF NOT is_worker
                OR NOT (OLD.status = 'awaiting_payment' AND NEW.status IN ('active', 'cancelled')
                    OR OLD.status = 'active' AND NEW.status = 'matured'
                    OR OLD.status = 'active' AND NEW.status = 'withdrawn'
                        AND OLD.withdrawal_requested_at IS NOT NULL
                    -- Undone only for a mistaken deposit, once two members of staff agreed.
                    OR OLD.status = 'active' AND NEW.status = 'unwound'
                        AND OLD.withdrawal_requested_at IS NULL
                        AND EXISTS (SELECT FROM investment_corrections c
                            WHERE c.investment_id = OLD.id AND c.kind = 'unwind'
                            AND c.status = 'approved')
                    -- Sent to another client, while the transfer waits for the worker.
                    OR OLD.status = 'active' AND NEW.status = 'transferred'
                        AND OLD.withdrawal_requested_at IS NULL
                        AND EXISTS (SELECT FROM investment_transfers t
                            WHERE t.investment_id = OLD.id AND t.status = 'waiting')) THEN
                RAISE EXCEPTION 'investment cannot go from % to % here', OLD.status, NEW.status
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.matured_at IS NOT NULL OR NEW.maturity_entry_id IS NOT NULL
            OR NEW.rolled_into_id IS NOT NULL OR NEW.payout_id IS NOT NULL
            OR NEW.payout_refusal IS NOT NULL OR NEW.withdrawn_at IS NOT NULL
            OR NEW.withdrawal_entry_id IS NOT NULL OR NEW.withdrawal_payout_id IS NOT NULL
            OR NEW.unwound_at IS NOT NULL OR NEW.unwind_entry_id IS NOT NULL
            OR NEW.transferred_at IS NOT NULL OR NEW.transfer_entry_id IS NOT NULL THEN
            RAISE EXCEPTION 'only maturity records what became of the money'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'active' AND (
            NEW.start_date <> OLD.start_date OR NEW.started_at <> OLD.started_at
            OR NEW.start_entry_id <> OLD.start_entry_id
            OR NEW.maturity_date IS DISTINCT FROM OLD.maturity_date) THEN
            RAISE EXCEPTION 'the start of an investment never changes' USING ERRCODE = '42501';
        END IF;
        -- Interest only ever goes up, a day at a time, and only the worker posts it.
        IF NEW.accrued_through IS DISTINCT FROM OLD.accrued_through
            OR NEW.accrued_gross_minor <> OLD.accrued_gross_minor
            OR NEW.accrued_tax_minor <> OLD.accrued_tax_minor THEN
            IF NOT is_worker OR OLD.status <> 'active'
                OR OLD.withdrawal_requested_at IS NOT NULL
                OR NEW.accrued_through IS NULL
                OR (OLD.accrued_through IS NOT NULL AND NEW.accrued_through <= OLD.accrued_through)
                OR NEW.accrued_gross_minor < OLD.accrued_gross_minor
                OR NEW.accrued_tax_minor < OLD.accrued_tax_minor THEN
                RAISE EXCEPTION 'interest is added by the daily run, forwards only'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        -- The client changes only the choice for maturity, and only before the maturity
        -- date. Staff change nothing here.
        IF NEW.at_maturity IS DISTINCT FROM OLD.at_maturity
            OR NEW.payout_destination_id IS DISTINCT FROM OLD.payout_destination_id THEN
            IF actor IS DISTINCT FROM 'client' OR OLD.withdrawal_requested_at IS NOT NULL THEN
                RAISE EXCEPTION 'only the client chooses what happens at maturity'
                    USING ERRCODE = '42501';
            END IF;
            IF OLD.maturity_date IS NOT NULL
                AND (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date >= OLD.maturity_date
            THEN
                RAISE EXCEPTION 'the choice for maturity closes the day before'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        -- Only the client asks to withdraw early, once, while it runs and before the day it
        -- matures. Interest stops at the request, so the amount they saw is the amount paid.
        IF NEW.withdrawal_requested_at IS DISTINCT FROM OLD.withdrawal_requested_at
            OR NEW.withdrawal_destination_id IS DISTINCT FROM OLD.withdrawal_destination_id THEN
            IF actor IS DISTINCT FROM 'client' OR OLD.status <> 'active'
                OR OLD.withdrawal_requested_at IS NOT NULL
                OR NEW.withdrawal_requested_at IS NULL OR NEW.withdrawal_destination_id IS NULL
                -- Not while a transfer of it is open.
                OR EXISTS (SELECT FROM investment_transfers t WHERE t.investment_id = OLD.id
                    AND t.status IN ('waiting', 'held', 'release_proposed'))
                OR abs(extract(epoch FROM NEW.withdrawal_requested_at - clock_timestamp())) > 300
            THEN
                RAISE EXCEPTION 'only the client asks, once, to withdraw a running investment'
                    USING ERRCODE = '42501';
            END IF;
            IF OLD.maturity_date IS NOT NULL
                AND (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date >= OLD.maturity_date
            THEN
                RAISE EXCEPTION 'early withdrawal closes the day before maturity'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

RULES = [
    "REVOKE ALL ON FUNCTION finvest_investment_transfers_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_transfers_guard BEFORE INSERT OR UPDATE "
    "ON investment_transfers FOR EACH ROW "
    "EXECUTE FUNCTION finvest_investment_transfers_guard()",
    "REVOKE ALL ON investment_transfers FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, release_proposed_by, release_proposed_at, "
    f"release_reason, released_by, released_at, declined_by, declined_at, decision_reason, "
    f"cancelled_at) ON investment_transfers TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (status, finished_at, sent_minor, received_investment_id, "
    f"kept_investment_id, entry_id, failure_code) ON investment_transfers TO {WORKER_ROLE}",
    f"GRANT UPDATE (transferred_at, transfer_entry_id) ON investments TO {WORKER_ROLE}",
    # A client sees the transfers they sent or received, and asks or cancels only their own.
    # Staff see every one. The worker reads and records outcomes.
    "ALTER TABLE investment_transfers ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY investment_transfers_app_read ON investment_transfers FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF} OR ({CLIENT} AND public.finvest_actor_id() "
    f"IN (sender_client_id, recipient_client_id)))",
    f"CREATE POLICY investment_transfers_app_insert ON investment_transfers FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({CLIENT} AND sender_client_id = public.finvest_actor_id())",
    f"CREATE POLICY investment_transfers_app_update ON investment_transfers FOR UPDATE "
    f"TO {APP_ROLE} USING ({STAFF} OR ({CLIENT} AND sender_client_id = public.finvest_actor_id())) "
    f"WITH CHECK ({STAFF} OR ({CLIENT} AND sender_client_id = public.finvest_actor_id()))",
    f"CREATE POLICY investment_transfers_worker_read ON investment_transfers FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY investment_transfers_worker_update ON investment_transfers FOR UPDATE "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, TRANSFERS_GUARD, *COLUMNS, GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_transfers)
        OR EXISTS (SELECT FROM investments WHERE status = 'transferred'
            OR source_type IN ('transfer_received', 'transfer_kept')) THEN
        RAISE EXCEPTION 'refusing to drop the record of investment transfers';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A transfer moved money between clients; a downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute(
        f"REVOKE UPDATE (transferred_at, transfer_entry_id) ON investments FROM {WORKER_ROLE}"
    )
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to restore the guard")
    earlier = script.get_revision("0066_investment_corrections")
    op.execute(earlier.module.GUARD)
    for constraint in (
        "ck_investments_source",
        "ck_investments_transferred",
        "ck_investments_started",
        "ck_investments_status",
    ):
        op.execute(f"ALTER TABLE investments DROP CONSTRAINT {constraint}")
    op.execute("DROP INDEX uq_investments_transfer_start")
    op.execute("DROP INDEX uq_investments_start_entry")
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT uq_investments_start_entry UNIQUE (start_entry_id)"
    )
    op.execute("ALTER TABLE investments DROP COLUMN transfer_entry_id")
    op.execute("ALTER TABLE investments DROP COLUMN transferred_at")
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_status CHECK (status IN "
        "('awaiting_payment', 'active', 'cancelled', 'matured', 'withdrawn', 'unwound'))"
    )
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_started CHECK ("
        "(status IN ('active', 'matured', 'withdrawn', 'unwound')) = (start_date IS NOT NULL) "
        "AND (start_date IS NULL) = (started_at IS NULL) "
        "AND (start_date IS NULL) = (start_entry_id IS NULL))"
    )
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_source CHECK ("
        "(source_type IS NULL AND source_id IS NULL) OR "
        "(source_type IN ('autoinvest_run', 'goal_saving') AND source_id IS NOT NULL))"
    )
    op.execute("DROP TABLE investment_transfers")
    op.execute("DROP FUNCTION finvest_investment_transfers_guard()")
