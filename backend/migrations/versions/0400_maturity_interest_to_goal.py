"""Save an investment's interest to a goal at maturity, and keep the principal invested.

Revision ID: 0400_maturity_interest_to_goal
Revises: 0410_custodian_statement_checks

A client may now choose `interest_to_goal` for what happens when a fixed term ends: the
principal rolls over into a new term in the same product, as for `pay_interest`, and the
interest after tax starts a second new investment at once, in the product the goal saves
into. The owner approved the design on 2026-11-14, and chose that the product's minimum does
not apply to these: the money is already invested, so no deposit is taken.

* `at_maturity` takes `interest_to_goal`, which is longer than the column allowed, and a new
  column `interest_goal_id` names the goal. It is set exactly when the choice is
  `interest_to_goal`, and a foreign key to the goal and the client means it can only ever be
  one of the client's own goals.
* The client changes `interest_goal_id` with the rest of the choice, under the same rules.
* A rolled-over term keeps the goal of the term it came from, and only the worker writes it
  so. What a sender keeps of a transfer keeps the goal; what a recipient gets never has one.
* `source_type` takes `goal_interest`, with `source_id` the investment that is maturing. The
  guard lets only the worker create one, while that investment still runs and has earned its
  last day, for exactly its interest after tax, in the live product the goal saves into, on
  that product's terms on the day, rolling over. The product's minimum is the one rule of a
  new investment it does not take.
* That investment starts with the same ledger entry as the rolled-over principal, so the
  unique index on `start_entry_id` leaves it out, as it does the two halves of a transfer.

The guard is the one in the database when this runs, with these changes made to its text, so
a change another migration made to it is kept. Each change is checked to apply exactly as many
times as expected, so a guard that has moved on refuses this migration rather than losing a
rule, and the downgrade takes back only these changes. See docs/investing.md, "Maturity".
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0400_maturity_interest_to_goal"
down_revision: str | None = "0410_custodian_statement_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

# 1. The investment that interest starts, before the rules for any other new investment.
_GOAL_INTEREST_ANCHOR = """        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0"""
_GOAL_INTEREST = """        IF TG_OP = 'INSERT' AND NEW.source_type = 'goal_interest' THEN
            -- The interest of an investment maturing now, saved to the goal its client chose:
            -- by the worker only, for exactly the interest after tax, in the live product the
            -- goal saves into, on that product's terms on the day, rolling over. The
            -- product's minimum does not apply (the owner's decision): the money is already
            -- invested, so no deposit is taken.
            SELECT * INTO source FROM investments WHERE id = NEW.source_id;
            SELECT * INTO product FROM investment_products WHERE id = NEW.product_id;
            day := (NEW.created_at AT TIME ZONE 'Africa/Kampala')::date;
            SELECT * INTO rate FROM investment_rates
                WHERE product_id = NEW.product_id AND effective_from <= day
                ORDER BY effective_from DESC LIMIT 1;
            IF NOT is_worker OR source.id IS NULL OR source.status <> 'active'
                OR source.at_maturity IS DISTINCT FROM 'interest_to_goal'
                OR source.withdrawal_requested_at IS NOT NULL
                OR source.maturity_date IS NULL
                OR source.accrued_through IS DISTINCT FROM source.maturity_date
                OR NEW.client_id <> source.client_id OR NEW.currency <> source.currency
                OR NEW.principal_minor <> source.accrued_gross_minor - source.accrued_tax_minor
                OR NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR NEW.rolled_from_id IS NOT NULL OR NEW.legacy_reference IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL OR NEW.interest_goal_id IS NOT NULL
                OR NEW.withdrawal_requested_at IS NOT NULL
                OR product.id IS NULL OR rate.id IS NULL OR product.status <> 'live'
                OR NEW.currency <> product.currency
                OR NEW.term_days IS DISTINCT FROM product.term_days
                OR NEW.at_maturity IS DISTINCT FROM
                    (CASE WHEN product.term_days IS NULL THEN NULL ELSE 'roll_over' END)
                OR NEW.annual_rate_bps <> rate.annual_rate_bps OR NEW.tax_bps <> rate.tax_bps
                OR NOT EXISTS (SELECT FROM goals g WHERE g.id = source.interest_goal_id
                    AND g.client_id = source.client_id AND g.status = 'active'
                    AND g.product_code = product.code)
            THEN
                RAISE EXCEPTION 'only maturity saves an investment''s interest to a goal'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
"""

# 2. Any other new investment names a goal only as a rollover of a term that named it.
_INSERT_BEFORE = """                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR (NEW.payout_destination_id IS NOT NULL AND NOT ("""
_INSERT_AFTER = """                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR (NEW.interest_goal_id IS NOT NULL AND NOT (
                    is_worker AND NEW.at_maturity = 'interest_to_goal'
                    AND EXISTS (SELECT FROM investments r WHERE r.id = NEW.rolled_from_id
                        AND r.at_maturity = 'interest_to_goal'
                        AND r.interest_goal_id = NEW.interest_goal_id)))
                OR (NEW.payout_destination_id IS NOT NULL AND NOT ("""

# 3. A transfer: the sender keeps their goal, the recipient gets none.
_RECEIVED_BEFORE = """                IF NEW.client_id <> transfer.recipient_client_id
                    OR NEW.payout_destination_id IS NOT NULL"""
_RECEIVED_AFTER = """                IF NEW.client_id <> transfer.recipient_client_id
                    OR NEW.payout_destination_id IS NOT NULL OR NEW.interest_goal_id IS NOT NULL"""
_KEPT_BEFORE = """                OR NEW.payout_destination_id IS DISTINCT FROM source.payout_destination_id
"""
_KEPT_AFTER = """                OR NEW.payout_destination_id IS DISTINCT FROM source.payout_destination_id
                OR NEW.interest_goal_id IS DISTINCT FROM source.interest_goal_id
"""

# 4. The goal is part of the choice, which the client changes.
_TERMS_BEFORE = ",payout_destination_id,matured_at,"
_TERMS_AFTER = ",payout_destination_id,interest_goal_id,matured_at,"
_CHOICE_BEFORE = """        IF NEW.at_maturity IS DISTINCT FROM OLD.at_maturity
            OR NEW.payout_destination_id IS DISTINCT FROM OLD.payout_destination_id THEN"""
_CHOICE_AFTER = """        IF NEW.at_maturity IS DISTINCT FROM OLD.at_maturity
            OR NEW.payout_destination_id IS DISTINCT FROM OLD.payout_destination_id
            OR NEW.interest_goal_id IS DISTINCT FROM OLD.interest_goal_id THEN"""

# (text before, text after, how many times it must appear)
_CHANGES: tuple[tuple[str, str, int], ...] = (
    (_GOAL_INTEREST_ANCHOR, _GOAL_INTEREST + _GOAL_INTEREST_ANCHOR, 1),
    (_INSERT_BEFORE, _INSERT_AFTER, 1),
    (_RECEIVED_BEFORE, _RECEIVED_AFTER, 1),
    (_KEPT_BEFORE, _KEPT_AFTER, 1),
    (_TERMS_BEFORE, _TERMS_AFTER, 2),
    (_CHOICE_BEFORE, _CHOICE_AFTER, 1),
)

AT_MATURITY = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_at_maturity CHECK ("
    "at_maturity IN ('roll_over', 'pay_out', 'pay_interest', 'interest_to_goal') "
    "AND (term_days IS NULL) = (at_maturity IS NULL))"
)
OLD_AT_MATURITY = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_at_maturity CHECK ("
    "at_maturity IN ('roll_over', 'pay_out', 'pay_interest') "
    "AND (term_days IS NULL) = (at_maturity IS NULL))"
)
INTEREST_GOAL = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_interest_goal CHECK ("
    "(at_maturity IS NOT DISTINCT FROM 'interest_to_goal') = (interest_goal_id IS NOT NULL))"
)
SOURCES = (
    "'autoinvest_run', 'goal_saving', 'transfer_received', 'transfer_kept', 'investment_booking'"
)
SOURCE = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_source CHECK ("
    "(source_type IS NULL AND source_id IS NULL) "
    "OR (source_type IN ({sources}) AND source_id IS NOT NULL))"
)
START_ENTRY = (
    "CREATE UNIQUE INDEX uq_investments_start_entry ON investments (start_entry_id) "
    "WHERE source_type IS DISTINCT FROM 'transfer_received' "
    "AND source_type IS DISTINCT FROM 'transfer_kept'{more}"
)


def _current_guard() -> str:
    """The investments guard as the database has it now, ready to run again."""
    definition = (
        op.get_bind()
        .execute(
            text("SELECT pg_get_functiondef('public.finvest_investments_guard()'::regprocedure)")
        )
        .scalar_one()
    )
    return str(definition)


def _replace(guard: str, before: str, after: str, times: int) -> str:
    if guard.count(before) != times:
        raise RuntimeError("the investments guard is not the one this migration expects")
    return guard.replace(before, after)


def guard() -> str:
    """The investments guard now in the database, with the goal for interest let through."""
    current = _current_guard()
    for before, after, times in _CHANGES:
        current = _replace(current, before, after, times)
    return current


def earlier_guard() -> str:
    """The investments guard now in the database, with this migration's changes taken back."""
    current = _current_guard()
    for before, after, times in reversed(_CHANGES):
        current = _replace(current, after, before, times)
    return current


def upgrade() -> None:
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_at_maturity")
    op.execute("ALTER TABLE investments ALTER COLUMN at_maturity TYPE varchar(16)")
    op.execute("ALTER TABLE investments ADD COLUMN interest_goal_id push_id")
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT fk_investments_interest_goal "
        "FOREIGN KEY (interest_goal_id, client_id) REFERENCES goals (id, client_id)"
    )
    op.execute(AT_MATURITY)
    op.execute(INTEREST_GOAL)
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_source")
    op.execute(SOURCE.format(sources=SOURCES + ", 'goal_interest'"))
    op.execute("DROP INDEX uq_investments_start_entry")
    op.execute(START_ENTRY.format(more=" AND source_type IS DISTINCT FROM 'goal_interest'"))
    # The interest of one maturity starts one saving, which the source pair already makes
    # unique; its start entry is the maturity's own.
    op.execute(
        "CREATE UNIQUE INDEX uq_investments_goal_interest_start ON investments (start_entry_id) "
        "WHERE source_type = 'goal_interest'"
    )
    # Investments saving their interest to a goal, for the goal's screen.
    op.execute(
        "CREATE INDEX ix_investments_interest_goal ON investments (interest_goal_id) "
        "WHERE interest_goal_id IS NOT NULL"
    )
    op.execute(f"GRANT UPDATE (interest_goal_id) ON investments TO {APP_ROLE}")
    op.execute(f"GRANT INSERT (interest_goal_id) ON investments TO {WORKER_ROLE}")
    op.execute(guard())


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investments
        WHERE at_maturity = 'interest_to_goal' OR source_type = 'goal_interest') THEN
        RAISE EXCEPTION 'refusing to drop the choice clients made to save their interest';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A client's choice for their money is never thrown away by a downgrade.
    op.execute(EMPTY_GUARD)
    op.execute(earlier_guard())
    op.execute(f"REVOKE INSERT (interest_goal_id) ON investments FROM {WORKER_ROLE}")
    op.execute(f"REVOKE UPDATE (interest_goal_id) ON investments FROM {APP_ROLE}")
    op.execute("DROP INDEX ix_investments_interest_goal")
    op.execute("DROP INDEX uq_investments_goal_interest_start")
    op.execute("DROP INDEX uq_investments_start_entry")
    op.execute(START_ENTRY.format(more=""))
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_source")
    op.execute(SOURCE.format(sources=SOURCES))
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_interest_goal")
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_at_maturity")
    op.execute("ALTER TABLE investments DROP CONSTRAINT fk_investments_interest_goal")
    op.execute("ALTER TABLE investments DROP COLUMN interest_goal_id")
    op.execute("ALTER TABLE investments ALTER COLUMN at_maturity TYPE varchar(12)")
    op.execute(OLD_AT_MATURITY)
