"""Record why a client sends an investment to another client.

Revision ID: 0243_transfer_purposes
Revises: 0261_payment_deposit_reversals

The owner chose, on a decision card, that a sender picks a reason from a short list, for anti
money laundering: family support, a gift, paying back a loan, business, or other with a few
words. `purpose` holds the choice and `purpose_note` the words, only for `other`.

Every transfer asked for from now on says why. Transfers saved before this have none, so the
column stays nullable and a trigger refuses a new one without it. Like everything else a
transfer was asked for with, the reason never changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0243_transfer_purposes"
down_revision: str | None = "0261_payment_deposit_reversals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURPOSES = ("family_support", "gift", "loan_repayment", "business", "other")

COLUMNS = [
    "ALTER TABLE investment_transfers ADD COLUMN purpose varchar(16)",
    "ALTER TABLE investment_transfers ADD COLUMN purpose_note varchar(140)",
    "ALTER TABLE investment_transfers ADD CONSTRAINT ck_investment_transfers_purpose CHECK ("
    "purpose IS NULL OR purpose IN (" + ", ".join(f"'{p}'" for p in PURPOSES) + "))",
    # Words only for `other`, and `other` always has some.
    "ALTER TABLE investment_transfers ADD CONSTRAINT ck_investment_transfers_purpose_note CHECK ("
    "(purpose_note IS NULL OR (purpose = 'other' AND btrim(purpose_note) <> '')) "
    "AND (purpose IS DISTINCT FROM 'other' OR purpose_note IS NOT NULL))",
]

GUARD = """
CREATE FUNCTION finvest_investment_transfer_purpose() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.purpose IS NULL THEN
            RAISE EXCEPTION 'a transfer says why it is sent' USING ERRCODE = '23514';
        END IF;
    ELSIF (NEW.purpose, NEW.purpose_note) IS DISTINCT FROM (OLD.purpose, OLD.purpose_note) THEN
        RAISE EXCEPTION 'why a transfer is sent never changes' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$
"""

TRIGGER = (
    "CREATE TRIGGER trg_investment_transfers_purpose BEFORE INSERT OR UPDATE "
    "ON investment_transfers FOR EACH ROW EXECUTE FUNCTION finvest_investment_transfer_purpose()"
)

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_transfers WHERE purpose IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop why investments were sent';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in [*COLUMNS, GUARD, TRIGGER]:
        op.execute(statement)


def downgrade() -> None:
    # Why money moved between two clients is kept for anti money laundering; never erased.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TRIGGER trg_investment_transfers_purpose ON investment_transfers")
    op.execute("DROP FUNCTION finvest_investment_transfer_purpose()")
    op.execute(
        "ALTER TABLE investment_transfers DROP CONSTRAINT ck_investment_transfers_purpose_note"
    )
    op.execute("ALTER TABLE investment_transfers DROP CONSTRAINT ck_investment_transfers_purpose")
    op.execute("ALTER TABLE investment_transfers DROP COLUMN purpose_note")
    op.execute("ALTER TABLE investment_transfers DROP COLUMN purpose")
