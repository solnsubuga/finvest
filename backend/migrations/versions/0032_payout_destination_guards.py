"""Tighten payout destinations after review.

Revision ID: 0032_payout_destination_guards
Revises: 0031_payouts

* One account, one client. An account approved for one client cannot be approved for another.
  The check is made at approval, which only staff see, so it tells a client nothing about
  anyone else's accounts.
* The approval time is the database's. A staff check is stamped within a few minutes of the
  database clock, so the 24-hour wait cannot be shortened by a wrong or forged time.
* Suspected fraud is final. A destination rejected for it cannot be proved again.
* A payout above the review threshold starts held. The threshold was applied only by the
  service; now the database refuses a new payout that is queued above the limits in force.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032_payout_destination_guards"
down_revision: str | None = "0031_payouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE_APPROVED = """
CREATE UNIQUE INDEX uq_payout_destinations_approved_account
    ON payout_destinations (kind, COALESCE(bank_code, ''), account_number)
    WHERE status = 'approved'
"""

GUARD_FUNCTION = """
CREATE FUNCTION finvest_payout_destinations_review_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF OLD.status = 'rejected' AND OLD.rejection_reason = 'suspected_fraud'
           AND NEW.status <> 'removed' THEN
            RAISE EXCEPTION 'a destination rejected for suspected fraud is final'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' AND OLD.status <> 'approved'
           AND abs(extract(epoch FROM NEW.approved_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'an approval is stamped with the current time'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

THRESHOLD_FUNCTION = """
CREATE FUNCTION finvest_payouts_review_threshold() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        threshold bigint;
    BEGIN
        SELECT review_above INTO threshold FROM withdrawal_limits
         WHERE effective_from <= NEW.business_date AND currency = NEW.currency
         ORDER BY effective_from DESC LIMIT 1;
        IF NEW.status = 'queued' AND threshold IS NOT NULL AND NEW.amount_minor > threshold THEN
            RAISE EXCEPTION 'a payout above the review threshold starts held'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""


def upgrade() -> None:
    op.execute(UNIQUE_APPROVED)
    op.execute(GUARD_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION finvest_payout_destinations_review_guard() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER payout_destinations_review_guard BEFORE UPDATE ON payout_destinations "
        "FOR EACH ROW EXECUTE FUNCTION finvest_payout_destinations_review_guard()"
    )
    op.execute(THRESHOLD_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION finvest_payouts_review_threshold() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER payouts_review_threshold BEFORE INSERT ON payouts "
        "FOR EACH ROW EXECUTE FUNCTION finvest_payouts_review_threshold()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER payouts_review_threshold ON payouts")
    op.execute("DROP FUNCTION finvest_payouts_review_threshold()")
    op.execute("DROP TRIGGER payout_destinations_review_guard ON payout_destinations")
    op.execute("DROP FUNCTION finvest_payout_destinations_review_guard()")
    op.execute("DROP INDEX uq_payout_destinations_approved_account")
