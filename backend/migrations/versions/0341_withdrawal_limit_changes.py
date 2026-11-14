"""Change the withdrawal limits and the review threshold on Settings, by two people.

Revision ID: 0341_withdrawal_limit_changes
Revises: 0340_platform_settings

The limits are versions in `withdrawal_limits` (migration 0031): each names who proposed it and
who approved it, and never changes. Until now nothing but the launch row existed, because no
route could make a version. `withdrawal_limit_changes` holds every proposed version: one member
of staff proposes the limits and the day they start, a different one approves, rejects or the
proposer withdraws, as for the trust account (migration 0260). Approving inserts the version in
`withdrawal_limits` in the same transaction, in the approver's name, and records it here, so the
proposal and the version it made can never part.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0341_withdrawal_limit_changes"
down_revision: str | None = "0340_platform_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE withdrawal_limit_changes (
    id push_id NOT NULL,
    effective_from date NOT NULL,
    currency varchar(3) NOT NULL,
    mobile_money_per_payout bigint NOT NULL,
    mobile_money_per_day bigint,
    bank_per_payout bigint,
    bank_per_day bigint,
    review_above bigint,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    -- The version an approval made.
    limits_id push_id,
    CONSTRAINT pk_withdrawal_limit_changes PRIMARY KEY (id),
    CONSTRAINT fk_withdrawal_limit_changes_limits
        FOREIGN KEY (limits_id) REFERENCES withdrawal_limits (id),
    CONSTRAINT uq_withdrawal_limit_changes_limits UNIQUE (limits_id),
    CONSTRAINT ck_withdrawal_limit_changes_currency CHECK (currency = 'UGX'),
    CONSTRAINT ck_withdrawal_limit_changes_mobile_money
        CHECK (mobile_money_per_payout BETWEEN 1 AND 5000000),
    CONSTRAINT ck_withdrawal_limit_changes_positive CHECK (
        COALESCE(mobile_money_per_day, 1) > 0 AND COALESCE(bank_per_payout, 1) > 0
        AND COALESCE(bank_per_day, 1) > 0 AND COALESCE(review_above, 1) > 0),
    CONSTRAINT ck_withdrawal_limit_changes_daily CHECK (
        COALESCE(mobile_money_per_day, mobile_money_per_payout) >= mobile_money_per_payout
        AND (bank_per_day IS NULL OR bank_per_payout IS NULL OR bank_per_day >= bank_per_payout)),
    CONSTRAINT ck_withdrawal_limit_changes_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_withdrawal_limit_changes_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_withdrawal_limit_changes_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)
        AND (status = 'approved') = (limits_id IS NOT NULL)),
    -- Two people. Only the proposer can withdraw, and never approve or reject, their own.
    CONSTRAINT ck_withdrawal_limit_changes_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
-- One change at a time, so an approval is always of the limits the approver read.
CREATE UNIQUE INDEX uq_withdrawal_limit_changes_open
    ON withdrawal_limit_changes (currency) WHERE status = 'proposed'
"""

GUARD = """
CREATE FUNCTION finvest_withdrawal_limit_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff change the withdrawal limits' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id()
                OR NEW.limits_id IS NOT NULL
            THEN
                RAISE EXCEPTION 'a limits change starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided limits change never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                     'limits_id'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                        'limits_id'])
        THEN
            RAISE EXCEPTION 'a limits change is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        -- An approval names the version it made, with exactly these limits, from this proposer.
        IF NEW.status = 'approved' AND NOT EXISTS (
            SELECT FROM withdrawal_limits l WHERE l.id = NEW.limits_id
                AND l.effective_from = NEW.effective_from AND l.currency = NEW.currency
                AND l.mobile_money_per_payout = NEW.mobile_money_per_payout
                AND l.mobile_money_per_day IS NOT DISTINCT FROM NEW.mobile_money_per_day
                AND l.bank_per_payout IS NOT DISTINCT FROM NEW.bank_per_payout
                AND l.bank_per_day IS NOT DISTINCT FROM NEW.bank_per_day
                AND l.review_above IS NOT DISTINCT FROM NEW.review_above
                AND l.proposed_by = NEW.proposed_by AND l.approved_by = NEW.decided_by)
        THEN
            RAISE EXCEPTION 'an approved limits change names the version it made'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_withdrawal_limit_changes_guard() FROM PUBLIC",
    "CREATE TRIGGER withdrawal_limit_changes_guard BEFORE INSERT OR UPDATE "
    "ON withdrawal_limit_changes "
    "FOR EACH ROW EXECUTE FUNCTION finvest_withdrawal_limit_changes_guard()",
    "REVOKE ALL ON withdrawal_limit_changes FROM PUBLIC",
    # No DELETE for anyone: who changed the limits, and when, is part of the record.
    "GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason, limits_id) "
    f"ON withdrawal_limit_changes TO {APP_ROLE}",
    f"GRANT SELECT ON withdrawal_limit_changes TO {WORKER_ROLE}",
    "ALTER TABLE withdrawal_limit_changes ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY withdrawal_limit_changes_app ON withdrawal_limit_changes "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff') "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
    "CREATE POLICY withdrawal_limit_changes_worker ON withdrawal_limit_changes FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM withdrawal_limit_changes) THEN
        RAISE EXCEPTION 'refusing to drop withdrawal limit changes that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Who changed the limits, and when, is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE withdrawal_limit_changes")
    op.execute("DROP FUNCTION finvest_withdrawal_limit_changes_guard()")
