"""Pay the interest at maturity, and keep the principal invested for another term.

Revision ID: 0333_maturity_pay_interest
Revises: 0303_goal_locks

A client may now choose `pay_interest` for what happens when a fixed term ends: the principal
rolls over into a new term in the same product, and the interest after tax is paid to the
payout destination they chose. The new term keeps that choice and destination, so the client
is paid the interest each term until they change it.

* `at_maturity` takes `pay_interest`, which is longer than the column allowed.
* A destination may be named for `pay_interest` as for `pay_out`.
* A matured investment whose principal rolled over may also carry the payout for its interest,
  or why the interest waits. When it waits, the client or the worker sends it on later, as for
  any held maturity money.
* The guard lets the worker start the rolled-over term with the same choice and destination
  as the term it came from, and nothing else.

The guard is the one migration 0241 wrote, with those two changes made to its text. Each change
is checked to apply exactly once, so a guard that has moved on refuses this migration rather
than losing a rule. See docs/investing.md, "Maturity".
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0333_maturity_pay_interest"
down_revision: str | None = "0303_goal_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARD_FROM = "0241_investment_transfers"

# A rolled-over term starts unpaid like any other. Its one difference: with `pay_interest`
# it keeps the destination of the term it came from.
_INSERT_BEFORE = """        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL THEN"""
_INSERT_AFTER = """        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR (NEW.payout_destination_id IS NOT NULL AND NOT (
                    is_worker AND NEW.at_maturity = 'pay_interest'
                    AND EXISTS (SELECT FROM investments r WHERE r.id = NEW.rolled_from_id
                        AND r.at_maturity = 'pay_interest'
                        AND r.payout_destination_id = NEW.payout_destination_id))) THEN"""

# Interest that waits after the principal rolled over is sent on like any held money.
_MATURED_BEFORE = """                OR OLD.payout_id IS NOT NULL OR OLD.rolled_into_id IS NOT NULL
                OR NEW.payout_id IS NULL OR NEW.payout_refusal IS NOT NULL"""
_MATURED_AFTER = """                OR OLD.payout_id IS NOT NULL
                OR (OLD.rolled_into_id IS NOT NULL AND OLD.at_maturity <> 'pay_interest')
                OR NEW.payout_id IS NULL OR NEW.payout_refusal IS NOT NULL"""

AT_MATURITY = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_at_maturity CHECK ("
    "at_maturity IN ('roll_over', 'pay_out', 'pay_interest') "
    "AND (term_days IS NULL) = (at_maturity IS NULL))"
)
DESTINATION = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_destination CHECK ("
    "payout_destination_id IS NULL OR at_maturity IN ('pay_out', 'pay_interest'))"
)
# A matured investment has the entry that closed it. Its money went one way, or, when the
# principal was kept invested, the principal into a new term and the interest one way.
MATURED = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_matured CHECK ("
    "(status = 'matured') = (matured_at IS NOT NULL) "
    "AND (status = 'matured') = (maturity_entry_id IS NOT NULL) "
    "AND (status <> 'matured' OR accrued_through = maturity_date) "
    "AND CASE WHEN status <> 'matured' "
    "THEN num_nonnulls(rolled_into_id, payout_id, payout_refusal) = 0 "
    "WHEN rolled_into_id IS NOT NULL AND at_maturity = 'pay_interest' "
    "THEN num_nonnulls(payout_id, payout_refusal) <= 1 "
    "ELSE num_nonnulls(rolled_into_id, payout_id, payout_refusal) = 1 END)"
)

OLD_AT_MATURITY = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_at_maturity CHECK ("
    "at_maturity IN ('roll_over', 'pay_out') AND (term_days IS NULL) = (at_maturity IS NULL))"
)
OLD_DESTINATION = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_destination CHECK ("
    "payout_destination_id IS NULL OR at_maturity = 'pay_out')"
)
OLD_MATURED = (
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_matured CHECK ("
    "(status = 'matured') = (matured_at IS NOT NULL) "
    "AND (status = 'matured') = (maturity_entry_id IS NOT NULL) "
    "AND (status = 'matured')::int = num_nonnulls(rolled_into_id, payout_id, payout_refusal) "
    "AND (status <> 'matured' OR accrued_through = maturity_date))"
)


def _earlier_guard() -> str:
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to build the guard")
    guard: str = script.get_revision(GUARD_FROM).module.GUARD
    return guard


def _replace_once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise RuntimeError("the investments guard is not the one this migration expects")
    return text.replace(before, after)


def guard() -> str:
    """The investments guard of migration 0241, letting `pay_interest` through."""
    text = _replace_once(_earlier_guard(), _INSERT_BEFORE, _INSERT_AFTER)
    return _replace_once(text, _MATURED_BEFORE, _MATURED_AFTER)


def upgrade() -> None:
    for name in ("ck_investments_at_maturity", "ck_investments_destination"):
        op.execute(f"ALTER TABLE investments DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_matured")
    op.execute("ALTER TABLE investments ALTER COLUMN at_maturity TYPE varchar(12)")
    for statement in (AT_MATURITY, DESTINATION, MATURED, guard()):
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investments WHERE at_maturity = 'pay_interest') THEN
        RAISE EXCEPTION 'refusing to drop the choice clients made to be paid their interest';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A client's choice for their money is never thrown away by a downgrade.
    op.execute(EMPTY_GUARD)
    op.execute(_earlier_guard())
    for name in ("ck_investments_at_maturity", "ck_investments_destination"):
        op.execute(f"ALTER TABLE investments DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_matured")
    op.execute("ALTER TABLE investments ALTER COLUMN at_maturity TYPE varchar(10)")
    for statement in (OLD_AT_MATURITY, OLD_DESTINATION, OLD_MATURED):
        op.execute(statement)
