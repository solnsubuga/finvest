"""Record what each payout pays: a withdrawal, or a Treasury bond's coupon, sale or repayment.

Revision ID: 0531_payout_purpose
Revises: 0530_treasury_bill_type_tax

A bond's coupon, the money from selling it early and its repayment at maturity are all paid out
through payouts, and the client was told each one as a withdrawal ("Your withdrawal has been
sent"). The purpose lets payouts tell them what the money is. It changes only the words, never
how the money moves, and it is fixed when the payout is made: a trigger refuses any change.

Bond payouts already made are found from the bond tables that name them, and a returned one sent
again from its source. They are marked as the migration owner with the payouts guard switched off
for this one statement, because the guard rightly refuses any change to a finished payout. Only
the new column is written.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0531_payout_purpose"
down_revision: str | None = "0530_treasury_bill_type_tax"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADD = """
ALTER TABLE payouts ADD COLUMN purpose text NOT NULL DEFAULT 'withdrawal'
    CONSTRAINT payouts_purpose_known
    CHECK (purpose IN ('withdrawal', 'bond_coupon', 'bond_sale', 'bond_repayment'))
"""

BACKFILL = """
UPDATE payouts p SET purpose = found.purpose
  FROM (
        SELECT payout_id AS id, 'bond_coupon' AS purpose FROM bond_coupons
         WHERE payout_id IS NOT NULL
        UNION ALL
        SELECT payout_id, 'bond_repayment' FROM bond_redemptions WHERE payout_id IS NOT NULL
        UNION ALL
        SELECT payout_id, 'bond_sale' FROM bond_sales WHERE payout_id IS NOT NULL
       ) AS found
 WHERE p.id = found.id
"""

# A returned bond payout sent again is funded from the same handover as the first.
RESENT = """
UPDATE payouts p SET purpose = first.purpose
  FROM payouts first
 WHERE first.purpose <> 'withdrawal' AND p.purpose = 'withdrawal'
   AND p.client_id = first.client_id AND p.source_reference = first.source_reference
"""

FIXED_FUNCTION = """
CREATE FUNCTION finvest_payout_purpose_fixed() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.purpose IS DISTINCT FROM OLD.purpose THEN
            RAISE EXCEPTION 'the purpose of a payout never changes' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""


def upgrade() -> None:
    op.execute(ADD)
    op.execute("ALTER TABLE payouts DISABLE TRIGGER payouts_guard")
    op.execute(BACKFILL)
    op.execute(RESENT)
    op.execute("ALTER TABLE payouts ENABLE TRIGGER payouts_guard")
    op.execute(FIXED_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION finvest_payout_purpose_fixed() FROM PUBLIC")
    # Named to sort before payouts_guard, so a change of purpose is refused for that reason.
    op.execute(
        "CREATE TRIGGER payouts_fixed_purpose BEFORE UPDATE ON payouts "
        "FOR EACH ROW EXECUTE FUNCTION finvest_payout_purpose_fixed()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER payouts_fixed_purpose ON payouts")
    op.execute("DROP FUNCTION finvest_payout_purpose_fixed()")
    op.execute("ALTER TABLE payouts DROP COLUMN purpose")
