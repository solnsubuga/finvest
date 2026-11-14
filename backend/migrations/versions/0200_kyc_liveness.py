"""Add liveness attempts, and let a passed attempt's capture become the check's selfie.

Revision ID: 0200_kyc_liveness
Revises: 0076_migration_carry_resolutions

* `kyc_liveness` holds one row per attempt. The client starts one; the worker asks the
  provider for a session, saves its page sealed, and records the answer.
* `kyc_photos.source` says where a photo came from: `client` (sent by the client) or
  `liveness` (the face a passed attempt captured). Only the worker adds a `liveness` photo, and
  only as a selfie. The photo trigger of migration 0050 still decides when photos may change,
  so a capture that arrives after the check was handed in is never added.

See docs/identity-checks.md#liveness.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0200_kyc_liveness"
down_revision: str | None = "0076_migration_carry_resolutions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE kyc_liveness (
    id push_id NOT NULL,
    check_id push_id NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'starting',
    provider varchar(32) NOT NULL,
    provider_request_id varchar(64) COLLATE "C",
    -- The provider's page carries a token, so it is sealed like the document number.
    url_sealed bytea,
    failure_code varchar(40) COLLATE "C",
    -- The selfie this attempt's capture became. NULL when the check had moved on.
    photo_id push_id,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_kyc_liveness PRIMARY KEY (id),
    CONSTRAINT fk_kyc_liveness_check FOREIGN KEY (check_id) REFERENCES kyc_checks (id),
    CONSTRAINT fk_kyc_liveness_photo FOREIGN KEY (photo_id) REFERENCES kyc_photos (id),
    CONSTRAINT ck_kyc_liveness_status CHECK (status IN (
        'starting', 'open', 'passed', 'failed', 'not_started', 'expired')),
    CONSTRAINT ck_kyc_liveness_open CHECK (
        status <> 'open' OR (provider_request_id IS NOT NULL AND url_sealed IS NOT NULL)),
    CONSTRAINT ck_kyc_liveness_finished CHECK (
        (status IN ('starting', 'open')) = (finished_at IS NULL)),
    CONSTRAINT ck_kyc_liveness_photo CHECK (photo_id IS NULL OR status = 'passed')
);
CREATE INDEX ix_kyc_liveness_check ON kyc_liveness (check_id, created_at);
ALTER TABLE kyc_photos ADD COLUMN source varchar(10) NOT NULL DEFAULT 'client';
ALTER TABLE kyc_photos ADD CONSTRAINT ck_kyc_photos_source CHECK (
    source = 'client' OR (source = 'liveness' AND kind = 'selfie'))
"""

# An attempt only moves forward, and a finished one never changes again. A client may only
# retire their own waiting attempt (when they start a new one); the worker does the rest.
GUARD = f"""
CREATE FUNCTION finvest_kyc_liveness_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
    BEGIN
        IF OLD.status NOT IN ('starting', 'open') THEN
            RAISE EXCEPTION 'a finished liveness attempt never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.check_id <> OLD.check_id OR NEW.provider <> OLD.provider
            OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a liveness attempt cannot move to another check' USING ERRCODE = '42501';
        END IF;
        IF actor = 'client' THEN
            IF NEW.status <> 'expired' OR (to_jsonb(NEW) - 'status' - 'updated_at' - 'finished_at')
                <> (to_jsonb(OLD) - 'status' - 'updated_at' - 'finished_at') THEN
                RAISE EXCEPTION 'a client may only retire a waiting liveness attempt'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NOT (actor = 'system' AND current_user = '{WORKER_ROLE}') THEN
            RAISE EXCEPTION 'only the worker records a liveness answer' USING ERRCODE = '42501';
        ELSIF OLD.status = 'open' AND NEW.status = 'starting' THEN
            RAISE EXCEPTION 'a liveness attempt never goes back' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_liveness_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_liveness_guard BEFORE UPDATE ON kyc_liveness "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_liveness_guard()",
    "REVOKE ALL ON kyc_liveness FROM PUBLIC",
    # No DELETE for anyone, as for the rest of an identity check.
    f"GRANT SELECT, INSERT, UPDATE (status, updated_at, finished_at) ON kyc_liveness TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE ON kyc_liveness TO {WORKER_ROLE}",
    "ALTER TABLE kyc_liveness ENABLE ROW LEVEL SECURITY",
    # The subquery runs under the kyc_checks policies, so a client sees their own attempts and
    # staff see all. Only the client starts an attempt.
    f"CREATE POLICY kyc_liveness_app_read ON kyc_liveness FOR SELECT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "USING (check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_liveness_app_insert ON kyc_liveness FOR INSERT TO {APP_ROLE} WITH CHECK ("  # noqa: S608 - constant role name
    "finvest_actor_kind() = 'client' AND status = 'starting' "
    "AND check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_liveness_app_update ON kyc_liveness FOR UPDATE TO {APP_ROLE} USING ("  # noqa: S608 - constant role name
    "finvest_actor_kind() = 'client' AND check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_liveness_worker ON kyc_liveness TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
    # A client sends only their own photos; a live capture is added by the worker.
    "DROP POLICY kyc_photos_app_insert ON kyc_photos",
    f"CREATE POLICY kyc_photos_app_insert ON kyc_photos FOR INSERT TO {APP_ROLE} WITH CHECK ("  # noqa: S608 - constant role name
    "finvest_actor_kind() = 'client' AND source = 'client' "
    "AND check_id IN (SELECT id FROM kyc_checks))",
    f"GRANT INSERT, UPDATE (replaced_at) ON kyc_photos TO {WORKER_ROLE}",
    f"CREATE POLICY kyc_photos_worker_insert ON kyc_photos FOR INSERT TO {WORKER_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'system' AND source = 'liveness' AND kind = 'selfie')",
    f"CREATE POLICY kyc_photos_worker_retire ON kyc_photos FOR UPDATE TO {WORKER_ROLE} "
    "USING (finvest_actor_kind() = 'system' AND kind = 'selfie') WITH CHECK (kind = 'selfie')",
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
    IF EXISTS (SELECT FROM kyc_liveness) OR EXISTS (
        SELECT FROM kyc_photos WHERE source = 'liveness') THEN
        RAISE EXCEPTION 'refusing to drop liveness records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Identity records are kept for the regulator; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    for statement in [
        "DROP POLICY kyc_photos_worker_retire ON kyc_photos",
        "DROP POLICY kyc_photos_worker_insert ON kyc_photos",
        f"REVOKE INSERT, UPDATE (replaced_at) ON kyc_photos FROM {WORKER_ROLE}",
        "DROP POLICY kyc_photos_app_insert ON kyc_photos",
        f"CREATE POLICY kyc_photos_app_insert ON kyc_photos FOR INSERT TO {APP_ROLE} WITH CHECK ("  # noqa: S608 - constant role name
        "finvest_actor_kind() = 'client' AND check_id IN (SELECT id FROM kyc_checks))",
        "ALTER TABLE kyc_photos DROP CONSTRAINT ck_kyc_photos_source",
        "ALTER TABLE kyc_photos DROP COLUMN source",
        "DROP TABLE kyc_liveness",
        "DROP FUNCTION finvest_kyc_liveness_guard()",
    ]:
        op.execute(statement)
