"""Add pose frames: the client's head turned left and right and tilted up and down.

Revision ID: 0371_kyc_pose_frames
Revises: 0410_custodian_statement_checks

With the guided selfie, the client takes a frame for each of four poses after the straight-on
selfie. The API keeps a frame only after the face quality service passed it, with the angles it
was taken at. Frames follow the selfie: they are added only while the selfie may change, the
newer frame of a pose retires the older, and a new selfie retires them all, so a set is always
taken together. A retired frame stays retired, and nothing is ever deleted.
See docs/identity-checks.md#guided-selfie.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0371_kyc_pose_frames"
down_revision: str | None = "0410_custodian_statement_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE kyc_pose_frames (
    id push_id NOT NULL,
    check_id push_id NOT NULL,
    pose varchar(8) NOT NULL,
    -- Where the file is in private object storage. Never a public link.
    storage_key varchar(200) COLLATE "C" NOT NULL,
    content_type varchar(32) NOT NULL,
    byte_size integer NOT NULL,
    sha256 char(64) COLLATE "C" NOT NULL,
    -- The angles the face quality service read, in whole degrees.
    yaw smallint NOT NULL,
    pitch smallint NOT NULL,
    created_at timestamptz NOT NULL,
    replaced_at timestamptz,
    CONSTRAINT pk_kyc_pose_frames PRIMARY KEY (id),
    CONSTRAINT fk_kyc_pose_frames_check FOREIGN KEY (check_id) REFERENCES kyc_checks (id),
    CONSTRAINT uq_kyc_pose_frames_storage_key UNIQUE (storage_key),
    CONSTRAINT ck_kyc_pose_frames_pose CHECK (pose IN ('left', 'right', 'up', 'down')),
    CONSTRAINT ck_kyc_pose_frames_content_type
        CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
    CONSTRAINT ck_kyc_pose_frames_byte_size CHECK (byte_size BETWEEN 1 AND 10485760),
    CONSTRAINT ck_kyc_pose_frames_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_kyc_pose_frames_angles CHECK (yaw BETWEEN -90 AND 90 AND pitch BETWEEN -90 AND 90)
);
CREATE UNIQUE INDEX uq_kyc_pose_frames_current ON kyc_pose_frames (check_id, pose)
    WHERE replaced_at IS NULL
"""

# The same rule as the selfie's (migration 0050): only while the check is a draft, or back with
# the client and the selfie asked for again.
GUARD = """
CREATE FUNCTION finvest_kyc_pose_frames_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        parent kyc_checks%ROWTYPE;
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF OLD.replaced_at IS NOT NULL OR NEW.replaced_at IS NULL
                OR (to_jsonb(NEW) - 'replaced_at') <> (to_jsonb(OLD) - 'replaced_at') THEN
                RAISE EXCEPTION 'a pose frame is only ever retired, once' USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.replaced_at IS NOT NULL THEN
            RAISE EXCEPTION 'a pose frame is only ever retired, once' USING ERRCODE = '42501';
        END IF;
        -- The lock makes a submit that is committing right now finish first.
        SELECT * INTO parent FROM kyc_checks WHERE id = NEW.check_id FOR SHARE;
        IF NOT FOUND OR NOT (
            parent.status = 'draft'
            OR (parent.status = 'awaiting_client' AND 'selfie' = ANY(parent.retake_photos))
        ) THEN
            RAISE EXCEPTION 'pose frames cannot change while the check is not with the client'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_pose_frames_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_pose_frames_guard BEFORE INSERT OR UPDATE ON kyc_pose_frames "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_pose_frames_guard()",
    "REVOKE ALL ON kyc_pose_frames FROM PUBLIC",
    # No DELETE for anyone: identity records are kept for the regulator's retention period.
    f"GRANT SELECT, INSERT, UPDATE (replaced_at) ON kyc_pose_frames TO {APP_ROLE}",
    f"GRANT SELECT ON kyc_pose_frames TO {WORKER_ROLE}",
    "ALTER TABLE kyc_pose_frames ENABLE ROW LEVEL SECURITY",
    # The subquery runs under the kyc_checks policies, so a client sees frames of their own
    # check and staff see all. Only the client adds or retires frames.
    f"CREATE POLICY kyc_pose_frames_app_read ON kyc_pose_frames FOR SELECT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "USING (check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_pose_frames_app_insert ON kyc_pose_frames FOR INSERT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "WITH CHECK (finvest_actor_kind() = 'client' AND check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_pose_frames_app_update ON kyc_pose_frames FOR UPDATE TO {APP_ROLE} "  # noqa: S608 - constant role name
    "USING (finvest_actor_kind() = 'client' AND check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_pose_frames_worker ON kyc_pose_frames FOR SELECT TO {WORKER_ROLE} "
    "USING (true)",
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
    IF EXISTS (SELECT FROM kyc_pose_frames) THEN
        RAISE EXCEPTION 'refusing to drop pose frames that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Identity records are kept for the regulator; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE kyc_pose_frames")
    op.execute("DROP FUNCTION finvest_kyc_pose_frames_guard()")
