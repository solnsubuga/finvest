"""The photos a client carried over from Finvest 1.0 takes when they claim their account.

Revision ID: 0053_kyc_claim_checks
Revises: 0038_payout_releases

A carried-over identity check has no photos, so at the claim the client takes a selfie and a
photo of the front of the identity document on file. One member of staff compares them with
each other and with the details carried over, and says whether they match (docs/identity-checks.md).

* `kyc_claim_checks` holds one claim check per carried-over identity check. It moves from
  draft to needs review, and from there to matched, not matched, or back to the client for
  named photos again. Matched and not matched are final and never change.
* `kyc_claim_photos` holds the photos, as `kyc_photos` does for a full check: a file in private
  storage, kept when replaced, added only while the claim check is the client's to change.
* A client creates a claim check only for their own approved, carried-over identity check.
  Staff decide. Nothing is deleted; the worker only reads.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0053_kyc_claim_checks"
down_revision: str | None = "0038_payout_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# New tables only.
zero_downtime = True

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE kyc_claim_checks (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    -- The approved identity check carried over from Finvest 1.0 that these photos are compared with.
    check_id push_id NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'draft',
    submission integer NOT NULL DEFAULT 0,
    submitted_at timestamptz,
    retake_photos text[] NOT NULL DEFAULT '{}',
    retake_requested_at timestamptz,
    client_message varchar(300),
    decided_at timestamptz,
    decided_by push_id,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_kyc_claim_checks PRIMARY KEY (id),
    CONSTRAINT uq_kyc_claim_checks_client UNIQUE (client_id),
    CONSTRAINT uq_kyc_claim_checks_check UNIQUE (check_id),
    CONSTRAINT fk_kyc_claim_checks_check FOREIGN KEY (check_id) REFERENCES kyc_checks (id),
    CONSTRAINT ck_kyc_claim_checks_status CHECK (status IN (
        'draft', 'needs_review', 'awaiting_client', 'matched', 'not_matched')),
    CONSTRAINT ck_kyc_claim_checks_submission CHECK (
        submission >= 0 AND (status = 'draft') = (submission = 0)
        AND (submission = 0) = (submitted_at IS NULL)),
    CONSTRAINT ck_kyc_claim_checks_decided CHECK (
        (status IN ('matched', 'not_matched')) = (decided_at IS NOT NULL)
        AND (decided_at IS NULL) = (decided_by IS NULL)),
    CONSTRAINT ck_kyc_claim_checks_retake CHECK (
        (status = 'awaiting_client') = (cardinality(retake_photos) > 0)
        AND (status = 'awaiting_client') = (retake_requested_at IS NOT NULL)),
    CONSTRAINT ck_kyc_claim_checks_retake_kinds CHECK (retake_photos <@ ARRAY['front', 'selfie'])
);
CREATE INDEX ix_kyc_claim_checks_queue ON kyc_claim_checks (submitted_at, id)
    WHERE status = 'needs_review';
CREATE TABLE kyc_claim_photos (
    id push_id NOT NULL,
    claim_check_id push_id NOT NULL,
    kind varchar(8) NOT NULL,
    -- Where the file is in private object storage. Never a public link.
    storage_key varchar(200) COLLATE "C" NOT NULL,
    content_type varchar(32) NOT NULL,
    byte_size integer NOT NULL,
    sha256 char(64) COLLATE "C" NOT NULL,
    created_at timestamptz NOT NULL,
    -- Set when the client takes the photo again. The old photo is kept as evidence.
    replaced_at timestamptz,
    CONSTRAINT pk_kyc_claim_photos PRIMARY KEY (id),
    CONSTRAINT fk_kyc_claim_photos_claim_check
        FOREIGN KEY (claim_check_id) REFERENCES kyc_claim_checks (id),
    CONSTRAINT uq_kyc_claim_photos_storage_key UNIQUE (storage_key),
    CONSTRAINT ck_kyc_claim_photos_kind CHECK (kind IN ('front', 'selfie')),
    CONSTRAINT ck_kyc_claim_photos_content_type
        CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
    CONSTRAINT ck_kyc_claim_photos_byte_size CHECK (byte_size BETWEEN 1 AND 10485760),
    CONSTRAINT ck_kyc_claim_photos_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
CREATE UNIQUE INDEX uq_kyc_claim_photos_current
    ON kyc_claim_photos (claim_check_id, kind) WHERE replaced_at IS NULL
"""

# The transitions and the columns each actor may change. The actor settings are a fence against
# bugs, not a credential (migration 0001); the worker never writes here.
GUARD = """
CREATE FUNCTION finvest_kyc_claim_checks_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
        before jsonb := pg_catalog.to_jsonb(OLD);
        changed text[];
        allowed boolean;
    BEGIN
        IF OLD.status IN ('matched', 'not_matched') THEN
            RAISE EXCEPTION 'a decided claim check never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id OR NEW.check_id <> OLD.check_id
            OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a claim check cannot move to another client' USING ERRCODE = '42501';
        END IF;
        SELECT coalesce(pg_catalog.array_agg(key), '{}') INTO changed
            FROM pg_catalog.jsonb_each(pg_catalog.to_jsonb(NEW))
            WHERE value IS DISTINCT FROM before -> key;
        IF actor = 'client' THEN
            -- Handing in starts a new submission and clears what staff asked for.
            allowed := OLD.status IN ('draft', 'awaiting_client') AND NEW.status = 'needs_review'
                AND NEW.submission = OLD.submission + 1
                AND changed <@ ARRAY[
                    'status', 'submission', 'submitted_at', 'retake_photos',
                    'retake_requested_at', 'client_message', 'updated_at'];
        ELSIF actor = 'staff' THEN
            allowed := OLD.status = 'needs_review'
                AND NEW.status IN ('matched', 'not_matched', 'awaiting_client')
                AND changed <@ ARRAY[
                    'status', 'retake_photos', 'retake_requested_at', 'client_message',
                    'decided_at', 'decided_by', 'updated_at'];
        ELSE
            allowed := false;
        END IF;
        IF NOT allowed THEN
            RAISE EXCEPTION 'claim check cannot go from % to % here', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# Photos follow their claim check, as identity photos follow theirs (migration 0050).
PHOTOS_GUARD = """
CREATE FUNCTION finvest_kyc_claim_photos_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        parent public.kyc_claim_checks%ROWTYPE;
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF OLD.replaced_at IS NOT NULL OR NEW.replaced_at IS NULL
                OR (pg_catalog.to_jsonb(NEW) - 'replaced_at')
                    <> (pg_catalog.to_jsonb(OLD) - 'replaced_at') THEN
                RAISE EXCEPTION 'a claim photo is only ever retired, once' USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.replaced_at IS NOT NULL THEN
            RAISE EXCEPTION 'a claim photo is only ever retired, once' USING ERRCODE = '42501';
        END IF;
        -- The lock makes a submit that is committing right now finish first.
        SELECT * INTO parent FROM public.kyc_claim_checks WHERE id = NEW.claim_check_id FOR SHARE;
        IF NOT FOUND OR NOT (
            parent.status = 'draft'
            OR (parent.status = 'awaiting_client' AND NEW.kind = ANY(parent.retake_photos))
        ) THEN
            RAISE EXCEPTION 'claim photos cannot change while the check is not with the client'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_claim_checks_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_claim_checks_guard BEFORE UPDATE ON kyc_claim_checks "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_claim_checks_guard()",
    "REVOKE ALL ON FUNCTION finvest_kyc_claim_photos_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_claim_photos_guard BEFORE INSERT OR UPDATE ON kyc_claim_photos "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_claim_photos_guard()",
    "REVOKE ALL ON kyc_claim_checks, kyc_claim_photos FROM PUBLIC",
    # No DELETE for anyone: identity records are kept for the regulator's retention period.
    f"GRANT SELECT, INSERT, UPDATE ON kyc_claim_checks TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (replaced_at) ON kyc_claim_photos TO {APP_ROLE}",
    f"GRANT SELECT ON kyc_claim_checks, kyc_claim_photos TO {WORKER_ROLE}",
    "ALTER TABLE kyc_claim_checks ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE kyc_claim_photos ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY kyc_claim_checks_app_read ON kyc_claim_checks FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    # Only for the client's own identity check, and only one carried over and approved.
    f"CREATE POLICY kyc_claim_checks_app_insert ON kyc_claim_checks FOR INSERT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
    "AND status = 'draft' AND check_id IN (SELECT id FROM kyc_checks "
    "WHERE client_id = finvest_actor_id() AND source = 'finvest1' AND status = 'approved'))",
    f"CREATE POLICY kyc_claim_checks_app_update ON kyc_claim_checks FOR UPDATE TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY kyc_claim_checks_worker ON kyc_claim_checks FOR SELECT TO {WORKER_ROLE} "
    "USING (true)",
    # The subquery runs under the policies above: a client reaches their own photos, staff all.
    f"CREATE POLICY kyc_claim_photos_app_read ON kyc_claim_photos FOR SELECT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "USING (claim_check_id IN (SELECT id FROM kyc_claim_checks))",
    f"CREATE POLICY kyc_claim_photos_app_insert ON kyc_claim_photos FOR INSERT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "WITH CHECK (finvest_actor_kind() = 'client' "
    "AND claim_check_id IN (SELECT id FROM kyc_claim_checks))",
    f"CREATE POLICY kyc_claim_photos_app_update ON kyc_claim_photos FOR UPDATE TO {APP_ROLE} "  # noqa: S608 - constant role name
    "USING (finvest_actor_kind() = 'client' AND claim_check_id IN (SELECT id FROM kyc_claim_checks))",
    f"CREATE POLICY kyc_claim_photos_worker ON kyc_claim_photos FOR SELECT TO {WORKER_ROLE} "
    "USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GUARD, PHOTOS_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_claim_checks) THEN
        RAISE EXCEPTION 'refusing to drop claim checks that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Identity records are kept for the regulator; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE kyc_claim_photos")
    op.execute("DROP TABLE kyc_claim_checks")
    op.execute("DROP FUNCTION finvest_kyc_claim_photos_guard()")
    op.execute("DROP FUNCTION finvest_kyc_claim_checks_guard()")
