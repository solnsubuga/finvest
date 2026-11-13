"""Record every attempt to send a payout, so that a payout is sent at most once.

Revision ID: 0033_payout_send_attempts
Revises: 0082_dealing_maturities

* The worker saves and commits an attempt before it calls the provider, and saves how it ended
  afterwards. An attempt is never deleted, and its outcome is written once.
* Only one attempt per payout may be open or end in anything but `deferred`, the provider's
  clear answer that it took nothing. So a payout that may have gone is never sent again.
* An attempt is only started for a queued payout. The worker sends mobile money only; a bank
  payout will record its attempt the same way when staff pay it from the trust account.
* A payout leaves `queued` for sent, paid, failed or uncertain only with such an attempt.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033_payout_send_attempts"
down_revision: str | None = "0082_dealing_maturities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE payout_send_attempts (
    payout_id push_id NOT NULL,
    attempt smallint NOT NULL,
    provider varchar(32) NOT NULL,
    started_at timestamptz NOT NULL,
    outcome varchar(16),
    provider_reference varchar(128),
    finished_at timestamptz,
    CONSTRAINT pk_payout_send_attempts PRIMARY KEY (payout_id, attempt),
    CONSTRAINT fk_payout_send_attempts_payout FOREIGN KEY (payout_id) REFERENCES payouts (id),
    CONSTRAINT ck_payout_send_attempts_attempt CHECK (attempt BETWEEN 1 AND 100),
    CONSTRAINT ck_payout_send_attempts_provider CHECK (provider ~ '^[a-z][a-z0-9_]{0,31}$'),
    CONSTRAINT ck_payout_send_attempts_outcome CHECK (outcome IN (
        'accepted', 'rejected', 'deferred', 'uncertain', 'interrupted')),
    CONSTRAINT ck_payout_send_attempts_finished CHECK ((outcome IS NULL) = (finished_at IS NULL))
);
CREATE UNIQUE INDEX uq_payout_send_attempts_one_that_may_have_gone
    ON payout_send_attempts (payout_id) WHERE outcome IS NULL OR outcome <> 'deferred'
"""

GUARD_FUNCTION = """
CREATE FUNCTION finvest_payout_send_attempts_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        payout_status text;
        last_attempt smallint;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a send attempt is never deleted' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT status INTO payout_status FROM public.payouts WHERE id = NEW.payout_id;
            IF payout_status IS DISTINCT FROM 'queued' THEN
                RAISE EXCEPTION 'only a queued payout is sent' USING ERRCODE = '42501';
            END IF;
            SELECT max(attempt) INTO last_attempt
              FROM public.payout_send_attempts WHERE payout_id = NEW.payout_id;
            IF NEW.attempt IS DISTINCT FROM COALESCE(last_attempt, 0) + 1
               OR NEW.outcome IS NOT NULL OR NEW.provider_reference IS NOT NULL THEN
                RAISE EXCEPTION 'a send attempt starts open, numbered after the last'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.outcome IS NOT NULL OR NEW.outcome IS NULL THEN
            RAISE EXCEPTION 'how a send attempt ended is written once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.payout_id, NEW.attempt, NEW.provider, NEW.started_at)
           IS DISTINCT FROM (OLD.payout_id, OLD.attempt, OLD.provider, OLD.started_at) THEN
            RAISE EXCEPTION 'a send attempt does not change' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

LEAVES_QUEUED_FUNCTION = """
CREATE FUNCTION finvest_payouts_need_a_send_attempt() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT FROM public.payout_send_attempts
             WHERE payout_id = NEW.id AND (outcome IS NULL OR outcome <> 'deferred')
        ) THEN
            RAISE EXCEPTION 'a payout leaves the queue only through a send attempt'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

T = "payout_send_attempts"
SYSTEM = "finvest_actor_kind() = 'system'"
STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payout_send_attempts_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_payouts_need_a_send_attempt() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payout_send_attempts_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payout_send_attempts_guard()",
    "CREATE TRIGGER payouts_need_a_send_attempt BEFORE UPDATE ON payouts FOR EACH ROW "
    "WHEN (OLD.status = 'queued' AND NEW.status IN ('sent', 'paid', 'failed', 'uncertain')) "
    "EXECUTE FUNCTION finvest_payouts_need_a_send_attempt()",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {WORKER_ROLE}",
    f"GRANT UPDATE (outcome, provider_reference, finished_at) ON {T} TO {WORKER_ROLE}",
    f"GRANT SELECT ON {T} TO {APP_ROLE}",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_worker_insert ON {T} FOR INSERT TO {WORKER_ROLE} WITH CHECK ({SYSTEM})",
    f"CREATE POLICY {T}_worker_update ON {T} FOR UPDATE TO {WORKER_ROLE} "
    f"USING ({SYSTEM}) WITH CHECK ({SYSTEM})",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD_FUNCTION, LEAVES_QUEUED_FUNCTION, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_send_attempts) THEN
        RAISE EXCEPTION 'refusing to drop payout send attempts';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TRIGGER payouts_need_a_send_attempt ON payouts")
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_payouts_need_a_send_attempt()")
    op.execute("DROP FUNCTION finvest_payout_send_attempts_guard()")
