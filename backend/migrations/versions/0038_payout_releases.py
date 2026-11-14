"""Release a held payout with two people.

Revision ID: 0038_payout_releases
Revises: 0171_payment_reassignments

A payout above the review threshold starts held. From the owner's standing rule that staff
operations that can be maker and checker must be, releasing it to be sent now takes two people:

* One member of staff proposes the release with a reason.
* A different member of staff approves it, in their own name, and in the same transaction the
  payout moves from held to queued. The proposer may withdraw it.
* Declining the payout stays a single person's step, like refusing a bank transfer: it moves no
  money out. It closes any open proposal, as declined, or as withdrawn when the proposer declines.
* The payouts guard admits a staff step from held to queued only with an approved release by the
  same person, and an approved release whose payout has not left held cannot commit.
* Nothing is updated except a decision, nothing is deleted, and a decided release never changes.
  Only staff see releases; the worker reads them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038_payout_releases"
down_revision: str | None = "0171_payment_reassignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "payout_releases"

TABLE = """
CREATE TABLE payout_releases (
    id push_id NOT NULL,
    payout_id push_id NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_payout_releases PRIMARY KEY (id),
    CONSTRAINT fk_payout_releases_payout FOREIGN KEY (payout_id) REFERENCES payouts (id),
    CONSTRAINT ck_payout_releases_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payout_releases_status
        CHECK (status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_payout_releases_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
    ),
    CONSTRAINT ck_payout_releases_two_people CHECK (
        status NOT IN ('approved', 'declined') OR decided_by <> proposed_by
    ),
    CONSTRAINT ck_payout_releases_withdrawn_by_proposer
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payout_releases_decision_reason
        CHECK (status = 'proposed' OR btrim(COALESCE(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_payout_releases_open ON payout_releases (payout_id)
    WHERE status = 'proposed';
CREATE UNIQUE INDEX uq_payout_releases_approved ON payout_releases (payout_id)
    WHERE status = 'approved'
"""

GUARD_FUNCTION = """
CREATE FUNCTION public.finvest_payout_releases_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a payout release is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff release a payout' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id()
               OR abs(extract(epoch FROM NEW.proposed_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a release starts proposed, by the person signed in, now'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (
                SELECT FROM public.payouts WHERE id = NEW.payout_id AND status = 'held'
            ) THEN
                RAISE EXCEPTION 'only a held payout is released' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed' THEN
            RAISE EXCEPTION 'a proposed release is decided once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.payout_id, NEW.reason, NEW.proposed_by, NEW.proposed_at)
           IS DISTINCT FROM (OLD.id, OLD.payout_id, OLD.reason, OLD.proposed_by, OLD.proposed_at)
        THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
           OR abs(extract(epoch FROM NEW.decided_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'a release is decided by the person signed in, now'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# At commit, an approved release's payout must have left held for queued.
RELEASED_FUNCTION = """
CREATE FUNCTION public.finvest_payout_releases_released() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF EXISTS (SELECT FROM public.payouts WHERE id = NEW.payout_id AND status = 'held') THEN
            RAISE EXCEPTION 'an approved release moves its payout in the same transaction'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

PAYOUTS_GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_payouts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
        step text := OLD.status || '>' || NEW.status;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'payouts are never deleted' USING ERRCODE = '42501';
        END IF;
        IF OLD.status IN ('paid', 'failed', 'cancelled') THEN
            RAISE EXCEPTION 'a finished payout never changes' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.client_id, NEW.destination_id, NEW.kind, NEW.amount_minor, NEW.currency,
            NEW.business_date, NEW.source_account_id, NEW.in_flight_account_id, NEW.request_key,
            NEW.source_reference, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.destination_id, OLD.kind, OLD.amount_minor, OLD.currency,
            OLD.business_date, OLD.source_account_id, OLD.in_flight_account_id, OLD.request_key,
            OLD.source_reference, OLD.created_at)
        THEN
            RAISE EXCEPTION 'the terms of a payout never change' USING ERRCODE = '42501';
        END IF;

        IF actor = 'client' AND step = 'held>cancelled' THEN
            IF NEW.reviewed_by IS NOT NULL THEN
                RAISE EXCEPTION 'a client signs no review' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN ('held>queued', 'held>cancelled') THEN
            IF NEW.reviewed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a review is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
            -- Released only by the second person, in the transaction where they approve
            -- someone else's proposal to release this very payout.
            IF step = 'held>queued' AND NOT EXISTS (
                SELECT FROM public.payout_releases
                 WHERE payout_id = NEW.id AND status = 'approved'
                   AND decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a held payout is released only with an approved release'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN (
            'sent>paid', 'sent>failed', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            -- Settled by hand from the statement: only by the second person, in the transaction
            -- where they approve someone else's proposal for this very outcome.
            IF NOT EXISTS (
                SELECT FROM public.payout_resolutions
                 WHERE payout_id = NEW.id AND status = 'approved' AND outcome = NEW.status
                   AND decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a payout is settled by hand only with an approved resolution'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'a resolution signs the resolution, not the review'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'system' AND step IN (
            'queued>queued', 'queued>sent', 'queued>paid', 'queued>failed', 'queued>uncertain',
            'sent>sent', 'sent>paid', 'sent>failed',
            'uncertain>uncertain', 'uncertain>sent', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'the worker signs nothing in a person''s name'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            RAISE EXCEPTION 'this step is not allowed for a payout' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# The guard as 0037 left it, restored on the way down.
OLD_PAYOUTS_GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_payouts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
        step text := OLD.status || '>' || NEW.status;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'payouts are never deleted' USING ERRCODE = '42501';
        END IF;
        IF OLD.status IN ('paid', 'failed', 'cancelled') THEN
            RAISE EXCEPTION 'a finished payout never changes' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.client_id, NEW.destination_id, NEW.kind, NEW.amount_minor, NEW.currency,
            NEW.business_date, NEW.source_account_id, NEW.in_flight_account_id, NEW.request_key,
            NEW.source_reference, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.destination_id, OLD.kind, OLD.amount_minor, OLD.currency,
            OLD.business_date, OLD.source_account_id, OLD.in_flight_account_id, OLD.request_key,
            OLD.source_reference, OLD.created_at)
        THEN
            RAISE EXCEPTION 'the terms of a payout never change' USING ERRCODE = '42501';
        END IF;

        IF actor = 'client' AND step = 'held>cancelled' THEN
            IF NEW.reviewed_by IS NOT NULL THEN
                RAISE EXCEPTION 'a client signs no review' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN ('held>queued', 'held>cancelled') THEN
            IF NEW.reviewed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a review is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN (
            'sent>paid', 'sent>failed', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            -- Settled by hand from the statement: only by the second person, in the transaction
            -- where they approve someone else's proposal for this very outcome.
            IF NOT EXISTS (
                SELECT FROM public.payout_resolutions
                 WHERE payout_id = NEW.id AND status = 'approved' AND outcome = NEW.status
                   AND decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a payout is settled by hand only with an approved resolution'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'a resolution signs the resolution, not the review'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'system' AND step IN (
            'queued>queued', 'queued>sent', 'queued>paid', 'queued>failed', 'queued>uncertain',
            'sent>sent', 'sent>paid', 'sent>failed',
            'uncertain>uncertain', 'uncertain>sent', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'the worker signs nothing in a person''s name'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            RAISE EXCEPTION 'this step is not allowed for a payout' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_payout_releases_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.finvest_payout_releases_released() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION public.finvest_payout_releases_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_payout_releases_guard()",
    f"CREATE CONSTRAINT TRIGGER {T}_released AFTER UPDATE ON {T} DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN (NEW.status = 'approved') "
    "EXECUTE FUNCTION public.finvest_payout_releases_released()",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason) ON {T} TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({STAFF} AND proposed_by = public.finvest_actor_id())",
    f"CREATE POLICY {T}_app_update ON {T} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD_FUNCTION, RELEASED_FUNCTION]:
        op.execute(statement)
    for statement in RULES:
        op.execute(statement)
    op.execute(PAYOUTS_GUARD)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_releases) THEN
        RAISE EXCEPTION 'refusing to drop the releases of payouts';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(OLD_PAYOUTS_GUARD)
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION public.finvest_payout_releases_released()")
    op.execute("DROP FUNCTION public.finvest_payout_releases_guard()")
