"""Stop a job with an external effect from running again after its lease expires.

A lease expires when the worker holding it crashed or stalled after its attempt started. For
most jobs the next worker simply runs the handler again. For a job that calls a provider that
would be a blind retry: the call may already have gone out. The worker now names those kinds
when it claims, and such a job fails as `uncertain_outcome` and is left for reconciliation.
"""

from alembic import op

revision = "0004_claim_external_effects"
down_revision = "0003_idempotency"
branch_labels = None
depends_on = None

OLD_SIGNATURE = "finvest_claim_jobs(text, text[], text, timestamptz, interval, integer)"
NEW_SIGNATURE = "finvest_claim_jobs(text, text[], text[], text, timestamptz, interval, integer)"

# The function from 0002_jobs, with two marked places that differ between the versions.
TEMPLATE = """
CREATE FUNCTION finvest_claim_jobs(
    p_queue text, p_kinds text[], --EXTERNAL_PARAMETER
    p_worker text, p_now timestamptz, p_lease interval, p_limit integer
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
            SELECT j.id, j.kind, j.status, j.attempts, j.max_attempts, j.expires_at
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
                --EXTERNAL_BRANCH
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

EXTERNAL_PARAMETER = "p_external_kinds text[],"
# The attempt started and nobody recorded its end, so the effect may have happened. This comes
# before the age and attempt checks: uncertain is the reason that matters to reconciliation.
EXTERNAL_BRANCH = """IF candidate.kind = ANY (p_external_kinds) THEN
                    UPDATE public.jobs
                    SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                        finished_at = p_now, updated_at = p_now,
                        last_error_code = 'uncertain_outcome', last_error_message = NULL
                    WHERE id = candidate.id;
                    CONTINUE;
                END IF;"""


def function(external_parameter: str, external_branch: str) -> str:
    return (
        TEMPLATE.replace("--EXTERNAL_PARAMETER", external_parameter)
        .replace("--EXTERNAL_BRANCH", external_branch)
        .strip()
    )


def replace(old: str, new: str, definition: str) -> None:
    op.execute(f"DROP FUNCTION {old}")
    op.execute(definition)
    op.execute(f"REVOKE ALL ON FUNCTION {new} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {new} TO finvest_worker")


def upgrade() -> None:
    replace(OLD_SIGNATURE, NEW_SIGNATURE, function(EXTERNAL_PARAMETER, EXTERNAL_BRANCH))


def downgrade() -> None:
    replace(NEW_SIGNATURE, OLD_SIGNATURE, function("", ""))
