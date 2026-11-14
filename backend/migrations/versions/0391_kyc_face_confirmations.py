"""Add face confirmations: a live selfie matched to the approved check before a sign-in change.

Revision ID: 0391_kyc_face_confirmations
Revises: 0390_staff_access_sync

`kyc_face_confirmations` holds one row per attempt. A client with an approved check starts one
from a signed-in session. The worker asks the liveness provider for a page, saves it sealed,
reads it until a live person was seen, stores the face it captured, and asks the face match
provider to compare it with the check's selfie. A passed attempt counts for that session for
ten minutes, and the identity module asks for one before a password or number change.

Only the worker records what a provider said. A client may only retire their own attempt that
is still waiting, which they do when they start a new one. A finished attempt never changes.
See docs/identity-checks.md#face-confirmations.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0391_kyc_face_confirmations"
down_revision: str | None = "0390_staff_access_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE kyc_face_confirmations (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    -- The client's approved check, and the selfie the capture is compared with: one on the
    -- check, or for a check carried over at the switch, the one on its matched claim.
    check_id push_id NOT NULL,
    reference_photo_id push_id,
    reference_claim_photo_id push_id,
    -- The identity session it was taken on. It confirms a change on that session only.
    session_id push_id NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'starting',
    provider varchar(32) NOT NULL,
    matcher varchar(32) NOT NULL,
    provider_request_id varchar(64) COLLATE "C",
    -- The provider's page carries a token, so it is sealed like the document number.
    url_sealed bytea,
    -- The face the liveness page captured, kept in private storage.
    capture_key varchar(200) COLLATE "C",
    capture_sha256 char(64) COLLATE "C",
    capture_content_type varchar(40) COLLATE "C",
    match_request_id varchar(64) COLLATE "C",
    -- The face match's confidence, as a whole percentage.
    score smallint,
    failure_code varchar(40) COLLATE "C",
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_kyc_face_confirmations PRIMARY KEY (id),
    CONSTRAINT fk_kyc_face_confirmations_check FOREIGN KEY (check_id) REFERENCES kyc_checks (id),
    CONSTRAINT fk_kyc_face_confirmations_photo
        FOREIGN KEY (reference_photo_id) REFERENCES kyc_photos (id),
    CONSTRAINT fk_kyc_face_confirmations_claim_photo
        FOREIGN KEY (reference_claim_photo_id) REFERENCES kyc_claim_photos (id),
    CONSTRAINT ck_kyc_face_confirmations_reference CHECK (
        (reference_photo_id IS NULL) <> (reference_claim_photo_id IS NULL)),
    CONSTRAINT ck_kyc_face_confirmations_status CHECK (status IN (
        'starting', 'open', 'matching', 'passed', 'failed', 'not_started', 'expired')),
    CONSTRAINT ck_kyc_face_confirmations_open CHECK (
        status <> 'open' OR (provider_request_id IS NOT NULL AND url_sealed IS NOT NULL)),
    CONSTRAINT ck_kyc_face_confirmations_capture CHECK (
        status NOT IN ('matching', 'passed') OR (
            capture_key IS NOT NULL AND capture_sha256 IS NOT NULL
            AND capture_content_type IS NOT NULL)),
    CONSTRAINT ck_kyc_face_confirmations_passed CHECK (
        status <> 'passed' OR (match_request_id IS NOT NULL AND score IS NOT NULL)),
    CONSTRAINT ck_kyc_face_confirmations_score CHECK (score IS NULL OR score BETWEEN 0 AND 100),
    CONSTRAINT ck_kyc_face_confirmations_finished CHECK (
        (status IN ('starting', 'open', 'matching')) = (finished_at IS NULL))
);
CREATE INDEX ix_kyc_face_confirmations_client ON kyc_face_confirmations (client_id, created_at)
"""

# Statuses only move forward: starting, then open, then matching, then an end.
GUARD = f"""
CREATE FUNCTION finvest_kyc_face_confirmations_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        steps constant text[] := ARRAY['starting', 'open', 'matching'];
    BEGIN
        IF OLD.status NOT IN ('starting', 'open', 'matching') THEN
            RAISE EXCEPTION 'a finished face confirmation never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id OR NEW.check_id <> OLD.check_id
            OR NEW.reference_photo_id IS DISTINCT FROM OLD.reference_photo_id
            OR NEW.reference_claim_photo_id IS DISTINCT FROM OLD.reference_claim_photo_id
            OR NEW.session_id <> OLD.session_id OR NEW.provider <> OLD.provider
            OR NEW.matcher <> OLD.matcher OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a face confirmation cannot change whose it is' USING ERRCODE = '42501';
        END IF;
        IF actor = 'client' THEN
            IF NEW.status <> 'expired' OR (to_jsonb(NEW) - 'status' - 'updated_at' - 'finished_at')
                <> (to_jsonb(OLD) - 'status' - 'updated_at' - 'finished_at') THEN
                RAISE EXCEPTION 'a client may only retire a waiting face confirmation'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NOT (actor = 'system' AND current_user = '{WORKER_ROLE}') THEN
            RAISE EXCEPTION 'only the worker records a face confirmation answer'
                USING ERRCODE = '42501';
        ELSIF NEW.status = ANY(steps)
            AND array_position(steps, NEW.status::text) < array_position(steps, OLD.status::text) THEN
            RAISE EXCEPTION 'a face confirmation never goes back' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_face_confirmations_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_face_confirmations_guard BEFORE UPDATE ON kyc_face_confirmations "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_face_confirmations_guard()",
    "REVOKE ALL ON kyc_face_confirmations FROM PUBLIC",
    # No DELETE for anyone, as for the rest of an identity check.
    f"GRANT SELECT, INSERT, UPDATE (status, updated_at, finished_at) ON kyc_face_confirmations "
    f"TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE ON kyc_face_confirmations TO {WORKER_ROLE}",
    "ALTER TABLE kyc_face_confirmations ENABLE ROW LEVEL SECURITY",
    # A client sees their own attempts and staff see all, as for the checks.
    f"CREATE POLICY kyc_face_confirmations_app_read ON kyc_face_confirmations FOR SELECT "
    f"TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    # Only the client starts one, against their own approved check. The subquery runs under
    # the kyc_checks policies, so it sees the client's own check only.
    f"CREATE POLICY kyc_face_confirmations_app_insert ON kyc_face_confirmations FOR INSERT "  # noqa: S608 - constant role name
    f"TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
    "AND status = 'starting' "
    "AND check_id IN (SELECT id FROM kyc_checks WHERE status = 'approved'))",
    f"CREATE POLICY kyc_face_confirmations_app_update ON kyc_face_confirmations FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY kyc_face_confirmations_worker ON kyc_face_confirmations TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_face_confirmations) THEN
        RAISE EXCEPTION 'refusing to drop face confirmations';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Identity records are kept for the regulator; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE kyc_face_confirmations")
    op.execute("DROP FUNCTION finvest_kyc_face_confirmations_guard()")
