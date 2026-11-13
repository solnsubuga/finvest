"""Create the outbox, durable jobs, attempts, consumer receipts and the scheduler's state."""

from alembic import op

revision = "0002_jobs"
down_revision = "0012_audit_events"
branch_labels = None
depends_on = None

# Payloads carry identifiers and codes, never personal data, account details or amounts tied to a
# person: a handler reads what it needs from the owning module's tables.
TABLES = """
CREATE TABLE outbox_events (
    id push_id NOT NULL,
    topic text NOT NULL,
    idempotency_key text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_outbox_events PRIMARY KEY (id),
    CONSTRAINT uq_outbox_events_topic_idempotency_key UNIQUE (topic, idempotency_key),
    CONSTRAINT ck_outbox_events_topic CHECK (topic ~ '^[a-z][a-z0-9_.]{0,99}$'),
    CONSTRAINT ck_outbox_events_idempotency_key
        CHECK (btrim(idempotency_key) <> '' AND length(idempotency_key) <= 200),
    CONSTRAINT ck_outbox_events_payload
        CHECK (jsonb_typeof(payload) = 'object' AND pg_column_size(payload) <= 65536)
);

CREATE TABLE jobs (
    id push_id NOT NULL,
    event_id push_id,
    queue text NOT NULL,
    kind text NOT NULL,
    idempotency_key text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL,
    run_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    lease_owner text,
    lease_expires_at timestamptz,
    fencing_token bigint NOT NULL DEFAULT 0,
    last_error_code text,
    last_error_message text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_jobs PRIMARY KEY (id),
    CONSTRAINT fk_jobs_event_id_outbox_events FOREIGN KEY (event_id) REFERENCES outbox_events (id),
    CONSTRAINT ck_jobs_queue CHECK (queue ~ '^[a-z][a-z0-9_]{0,63}$'),
    CONSTRAINT ck_jobs_kind CHECK (kind ~ '^[a-z][a-z0-9_.]{0,99}$'),
    CONSTRAINT ck_jobs_idempotency_key
        CHECK (btrim(idempotency_key) <> '' AND length(idempotency_key) <= 320),
    CONSTRAINT ck_jobs_payload
        CHECK (jsonb_typeof(payload) = 'object' AND pg_column_size(payload) <= 65536),
    CONSTRAINT ck_jobs_status CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    CONSTRAINT ck_jobs_attempts
        CHECK (max_attempts BETWEEN 1 AND 100 AND attempts BETWEEN 0 AND max_attempts),
    CONSTRAINT ck_jobs_lease CHECK (
        (status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    CONSTRAINT ck_jobs_finished CHECK ((status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)),
    CONSTRAINT ck_jobs_error CHECK (
        length(last_error_code) <= 64 AND length(last_error_message) <= 500
    )
);
-- One active job per logical delivery. A finished job may be followed by a new one, and the
-- consumer receipt then stops its result being applied twice.
CREATE UNIQUE INDEX uq_jobs_active_kind_idempotency_key
    ON jobs (kind, idempotency_key) WHERE status IN ('pending', 'running');
CREATE INDEX ix_jobs_due ON jobs (queue, run_at) WHERE status = 'pending';
CREATE INDEX ix_jobs_leases ON jobs (queue, lease_expires_at) WHERE status = 'running';
CREATE INDEX ix_jobs_failed ON jobs (finished_at) WHERE status = 'failed';
CREATE INDEX ix_jobs_event_id ON jobs (event_id) WHERE event_id IS NOT NULL;

CREATE TABLE job_attempts (
    job_id push_id NOT NULL,
    attempt integer NOT NULL,
    fencing_token bigint NOT NULL,
    worker text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    outcome text,
    error_code text,
    error_message text,
    CONSTRAINT pk_job_attempts PRIMARY KEY (job_id, attempt),
    CONSTRAINT fk_job_attempts_job_id_jobs FOREIGN KEY (job_id) REFERENCES jobs (id),
    CONSTRAINT ck_job_attempts_attempt CHECK (attempt >= 1),
    CONSTRAINT ck_job_attempts_outcome
        CHECK (outcome IN ('succeeded', 'retry', 'failed', 'lease_expired')),
    CONSTRAINT ck_job_attempts_finished CHECK ((outcome IS NULL) = (finished_at IS NULL)),
    CONSTRAINT ck_job_attempts_error CHECK (length(error_code) <= 64 AND length(error_message) <= 500)
);

-- Receipts outlive jobs: the durable record that a consumer applied a logical result once.
CREATE TABLE consumer_receipts (
    consumer text NOT NULL,
    idempotency_key text NOT NULL,
    job_id push_id NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_consumer_receipts PRIMARY KEY (consumer, idempotency_key),
    CONSTRAINT fk_consumer_receipts_job_id_jobs FOREIGN KEY (job_id) REFERENCES jobs (id),
    CONSTRAINT ck_consumer_receipts_result
        CHECK (jsonb_typeof(result) = 'object' AND pg_column_size(result) <= 65536)
);

CREATE TABLE scheduler_leadership (
    name text NOT NULL,
    holder text NOT NULL,
    fencing_token bigint NOT NULL,
    expires_at timestamptz NOT NULL,
    acquired_at timestamptz NOT NULL,
    renewed_at timestamptz NOT NULL,
    CONSTRAINT pk_scheduler_leadership PRIMARY KEY (name),
    CONSTRAINT ck_scheduler_leadership_fencing_token CHECK (fencing_token >= 1)
);

CREATE TABLE schedule_progress (
    name text NOT NULL,
    next_fire_at timestamptz,
    last_fire_at timestamptz,
    dispatched_count bigint NOT NULL DEFAULT 0,
    skipped_count bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_schedule_progress PRIMARY KEY (name),
    CONSTRAINT ck_schedule_progress_name CHECK (name ~ '^[a-z][a-z0-9_.]{0,99}$')
);

-- One row per schedule and fire time: the reason a daily run can never be created twice.
CREATE TABLE schedule_instances (
    schedule_name text NOT NULL,
    scheduled_for timestamptz NOT NULL,
    job_id push_id NOT NULL,
    leader_token bigint NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_schedule_instances PRIMARY KEY (schedule_name, scheduled_for),
    CONSTRAINT fk_schedule_instances_schedule_name_schedule_progress
        FOREIGN KEY (schedule_name) REFERENCES schedule_progress (name),
    CONSTRAINT fk_schedule_instances_job_id_jobs FOREIGN KEY (job_id) REFERENCES jobs (id)
)
"""

# Claims due jobs and expired leases under row locks that other workers skip. Each claim raises
# the fencing token, so the previous holder's late result is refused. Runs with the caller's
# rights: only the worker role may execute it.
CLAIM_FUNCTION = """
CREATE FUNCTION finvest_claim_jobs(
    p_queue text, p_kinds text[], p_worker text, p_now timestamptz, p_lease interval,
    p_limit integer
) RETURNS TABLE (
    claimed_id push_id, claimed_event_id push_id, claimed_kind text,
    claimed_idempotency_key text, claimed_payload jsonb, claimed_attempt integer,
    claimed_max_attempts integer, claimed_fencing_token bigint,
    claimed_lease_expires_at timestamptz, claimed_expires_at timestamptz
)
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog, public
    AS $$
    DECLARE
        candidate record;
    BEGIN
        IF p_limit NOT BETWEEN 1 AND 100
            OR p_lease NOT BETWEEN interval '1 second' AND interval '1 hour'
            OR p_now IS NULL OR coalesce(btrim(p_worker), '') = '' THEN
            RAISE EXCEPTION 'invalid job claim' USING ERRCODE = '22023';
        END IF;
        FOR candidate IN
            SELECT j.id, j.status, j.attempts, j.max_attempts, j.expires_at
            FROM public.jobs j
            WHERE j.queue = p_queue AND j.kind = ANY (p_kinds)
              AND ((j.status = 'pending' AND j.run_at <= p_now)
                   OR (j.status = 'running' AND j.lease_expires_at <= p_now))
            ORDER BY j.run_at, j.id
            LIMIT p_limit
            FOR UPDATE SKIP LOCKED
        LOOP
            IF candidate.status = 'running' THEN
                -- The previous holder crashed or stalled. Its late result is now fenced out.
                UPDATE public.job_attempts
                SET outcome = 'lease_expired', finished_at = p_now, error_code = 'lease_expired'
                WHERE job_id = candidate.id AND attempt = candidate.attempts AND outcome IS NULL;
            END IF;
            IF candidate.expires_at <= p_now OR candidate.attempts >= candidate.max_attempts THEN
                UPDATE public.jobs
                SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                    finished_at = p_now, updated_at = p_now,
                    last_error_code = CASE WHEN candidate.expires_at <= p_now
                        THEN 'max_age_exceeded' ELSE 'attempts_exhausted' END
                WHERE id = candidate.id;
                CONTINUE;
            END IF;
            UPDATE public.jobs
            SET status = 'running', attempts = attempts + 1, fencing_token = fencing_token + 1,
                lease_owner = p_worker, lease_expires_at = p_now + p_lease, updated_at = p_now
            WHERE id = candidate.id
            RETURNING id, event_id, kind, idempotency_key, payload, attempts, max_attempts,
                fencing_token, lease_expires_at, expires_at
            INTO claimed_id, claimed_event_id, claimed_kind, claimed_idempotency_key,
                claimed_payload, claimed_attempt, claimed_max_attempts, claimed_fencing_token,
                claimed_lease_expires_at, claimed_expires_at;
            INSERT INTO public.job_attempts (job_id, attempt, fencing_token, worker, started_at)
            VALUES (claimed_id, claimed_attempt, claimed_fencing_token, p_worker, p_now);
            RETURN NEXT;
        END LOOP;
    END
    $$
"""
CLAIM_SIGNATURE = "finvest_claim_jobs(text, text[], text, timestamptz, interval, integer)"

# Nobody may delete a row here: the job history is part of the record. The API only adds work,
# inside its business transaction. Everything after that belongs to the worker.
GRANTS = [
    "REVOKE ALL ON outbox_events, jobs, job_attempts, consumer_receipts, scheduler_leadership, "
    "schedule_progress, schedule_instances FROM PUBLIC",
    "GRANT INSERT ON outbox_events, jobs TO finvest_app",
    # ON CONFLICT reads the columns of the index it names, so the API may read those and no
    # others. They hold kinds and keys. Payloads, errors and state stay out of its reach.
    "GRANT SELECT (kind, idempotency_key, status) ON jobs TO finvest_app",
    "GRANT SELECT (topic, idempotency_key) ON outbox_events TO finvest_app",
    "GRANT SELECT, INSERT ON outbox_events, consumer_receipts, schedule_instances TO finvest_worker",
    "GRANT SELECT, INSERT, UPDATE ON jobs, job_attempts, scheduler_leadership, schedule_progress "
    "TO finvest_worker",
    f"REVOKE ALL ON FUNCTION {CLAIM_SIGNATURE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION {CLAIM_SIGNATURE} TO finvest_worker",
]


def upgrade() -> None:
    # asyncpg prepares each statement, so a revision executes one statement at a time.
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(CLAIM_FUNCTION.strip())
    for statement in GRANTS:
        op.execute(statement)


# The outbox, the job history and the receipts say what was sent and applied. A downgrade that
# dropped them with rows inside would let work be sent or applied a second time.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM outbox_events) OR EXISTS (SELECT FROM jobs)
        OR EXISTS (SELECT FROM consumer_receipts) OR EXISTS (SELECT FROM schedule_instances)
        OR EXISTS (SELECT FROM schedule_progress) THEN
        RAISE EXCEPTION 'refusing to drop job tables that hold rows';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(f"DROP FUNCTION {CLAIM_SIGNATURE}")
    op.execute(
        "DROP TABLE schedule_instances, schedule_progress, scheduler_leadership, "
        "consumer_receipts, job_attempts, jobs, outbox_events"
    )
