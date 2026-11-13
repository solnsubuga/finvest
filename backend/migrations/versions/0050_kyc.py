"""Add identity checks and their photos.

Revision ID: 0050_kyc
Revises: 0004_claim_external_effects

One check per client. A trigger repeats the transition table from
app/modules/kyc/domain/checks.py, so only staff, or the worker after the provider answered, can
approve, and an approved or rejected check never changes again. Row policies show a client only
their own check and photos. See docs/identity-checks.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0050_kyc"
down_revision: str | None = "0004_claim_external_effects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE kyc_checks (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'draft',
    document_type varchar(20) NOT NULL,
    document_country char(2) COLLATE "C" NOT NULL,
    surname varchar(100),
    given_names varchar(100),
    document_number varchar(32) COLLATE "C",
    date_of_birth date,
    document_expires_on date,
    tin varchar(10) COLLATE "C",
    details_confirmed_at timestamptz,
    -- Counts submissions, so each one has its own idempotency key at the provider.
    submission integer NOT NULL DEFAULT 0,
    submitted_at timestamptz,
    provider varchar(32),
    provider_reference varchar(200),
    automatic_checks jsonb NOT NULL DEFAULT '[]'::jsonb,
    review_flags text[] NOT NULL DEFAULT '{}',
    retake_photos text[] NOT NULL DEFAULT '{}',
    retake_requested_at timestamptz,
    client_message varchar(300),
    decided_at timestamptz,
    -- The staff member who decided. NULL when the automatic checks approved.
    decided_by push_id,
    account_number varchar(10) COLLATE "C",
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_kyc_checks PRIMARY KEY (id),
    CONSTRAINT uq_kyc_checks_client UNIQUE (client_id),
    CONSTRAINT uq_kyc_checks_account_number UNIQUE (account_number),
    -- A number reaches a client only through the registry, so it was never issued before.
    CONSTRAINT fk_kyc_checks_account_number
        FOREIGN KEY (account_number) REFERENCES account_numbers (number),
    CONSTRAINT ck_kyc_checks_status CHECK (status IN (
        'draft', 'submitted', 'needs_review', 'with_compliance', 'awaiting_client',
        'approved', 'rejected')),
    CONSTRAINT ck_kyc_checks_document_type CHECK (document_type IN (
        'national_id', 'passport', 'driving_permit', 'refugee_id')),
    CONSTRAINT ck_kyc_checks_document_country CHECK (document_country ~ '^[A-Z]{2}$'),
    CONSTRAINT ck_kyc_checks_document_number CHECK (document_number ~ '^[A-Z0-9]{5,32}$'),
    CONSTRAINT ck_kyc_checks_tin CHECK (tin ~ '^[0-9]{10}$'),
    CONSTRAINT ck_kyc_checks_submission CHECK (submission >= 0),
    -- Nothing leaves draft without confirmed details.
    CONSTRAINT ck_kyc_checks_details CHECK (
        status = 'draft' OR (
            details_confirmed_at IS NOT NULL AND surname IS NOT NULL AND given_names IS NOT NULL
            AND document_number IS NOT NULL AND date_of_birth IS NOT NULL
            AND document_expires_on IS NOT NULL AND submitted_at IS NOT NULL AND submission > 0)),
    -- Approved clients, and only they, hold an account number.
    CONSTRAINT ck_kyc_checks_account_number CHECK ((status = 'approved') = (account_number IS NOT NULL)),
    CONSTRAINT ck_kyc_checks_decided CHECK (
        (status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)),
    CONSTRAINT ck_kyc_checks_retake CHECK (
        (status = 'awaiting_client') = (cardinality(retake_photos) > 0)
        AND (status = 'awaiting_client') = (retake_requested_at IS NOT NULL)),
    CONSTRAINT ck_kyc_checks_retake_kinds
        CHECK (retake_photos <@ ARRAY['front', 'back', 'selfie']),
    CONSTRAINT ck_kyc_checks_automatic_checks CHECK (
        jsonb_typeof(automatic_checks) = 'array' AND pg_column_size(automatic_checks) <= 4096)
);
CREATE INDEX ix_kyc_checks_queue ON kyc_checks (status, submitted_at, id)
    WHERE status IN ('submitted', 'needs_review', 'with_compliance', 'awaiting_client');
-- One approved client per identity document.
CREATE UNIQUE INDEX uq_kyc_checks_approved_document
    ON kyc_checks (document_country, document_type, document_number) WHERE status = 'approved';
CREATE TABLE kyc_photos (
    id push_id NOT NULL,
    check_id push_id NOT NULL,
    kind varchar(8) NOT NULL,
    -- Where the file is in private object storage. Never a public link.
    storage_key varchar(200) COLLATE "C" NOT NULL,
    content_type varchar(32) NOT NULL,
    byte_size integer NOT NULL,
    sha256 char(64) COLLATE "C" NOT NULL,
    created_at timestamptz NOT NULL,
    -- Set when the client takes the photo again. The old photo is kept as evidence.
    replaced_at timestamptz,
    CONSTRAINT pk_kyc_photos PRIMARY KEY (id),
    CONSTRAINT fk_kyc_photos_check FOREIGN KEY (check_id) REFERENCES kyc_checks (id),
    CONSTRAINT uq_kyc_photos_storage_key UNIQUE (storage_key),
    CONSTRAINT ck_kyc_photos_kind CHECK (kind IN ('front', 'back', 'selfie')),
    CONSTRAINT ck_kyc_photos_content_type
        CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
    CONSTRAINT ck_kyc_photos_byte_size CHECK (byte_size BETWEEN 1 AND 10485760),
    CONSTRAINT ck_kyc_photos_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
CREATE UNIQUE INDEX uq_kyc_photos_current ON kyc_photos (check_id, kind) WHERE replaced_at IS NULL
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001), so the
# system's transitions also require the worker's database role.
TRANSITIONS = f"""
CREATE FUNCTION finvest_kyc_checks_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        allowed boolean;
    BEGIN
        IF OLD.status IN ('approved', 'rejected') THEN
            RAISE EXCEPTION 'a decided identity check never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'an identity check cannot move to another client' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = OLD.status THEN
            -- A client changes photos and details only while the check is theirs to change.
            allowed := actor IS DISTINCT FROM 'client' OR OLD.status IN ('draft', 'awaiting_client');
        ELSIF actor = 'client' THEN
            allowed := OLD.status IN ('draft', 'awaiting_client')
                AND NEW.status IN ('submitted', 'needs_review');
        ELSIF actor = 'staff' THEN
            allowed := (OLD.status = 'needs_review'
                    AND NEW.status IN ('approved', 'rejected', 'awaiting_client', 'with_compliance'))
                OR (OLD.status = 'with_compliance'
                    AND NEW.status IN ('approved', 'rejected', 'awaiting_client'));
        ELSIF actor = 'system' AND current_user = '{WORKER_ROLE}' THEN
            allowed := OLD.status = 'submitted' AND NEW.status IN ('approved', 'needs_review');
        ELSE
            allowed := false;
        END IF;
        IF NOT allowed THEN
            RAISE EXCEPTION 'identity check cannot go from % to % here', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# Photos follow their check: they are added or retired only while the check is the client's to
# change, and only the photos staff asked for once a check came back. A retired photo stays
# retired, so what staff saw during review can never be swapped afterwards.
PHOTOS_GUARD = """
CREATE FUNCTION finvest_kyc_photos_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        parent kyc_checks%ROWTYPE;
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF OLD.replaced_at IS NOT NULL OR NEW.replaced_at IS NULL
                OR (to_jsonb(NEW) - 'replaced_at') <> (to_jsonb(OLD) - 'replaced_at') THEN
                RAISE EXCEPTION 'an identity photo is only ever retired, once' USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.replaced_at IS NOT NULL THEN
            RAISE EXCEPTION 'an identity photo is only ever retired, once' USING ERRCODE = '42501';
        END IF;
        -- The lock makes a submit that is committing right now finish first.
        SELECT * INTO parent FROM kyc_checks WHERE id = NEW.check_id FOR SHARE;
        IF NOT FOUND OR NOT (
            parent.status = 'draft'
            OR (parent.status = 'awaiting_client' AND NEW.kind = ANY(parent.retake_photos))
        ) THEN
            RAISE EXCEPTION 'identity photos cannot change while the check is not with the client'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_photos_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_photos_guard BEFORE INSERT OR UPDATE ON kyc_photos "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_photos_guard()",
    "REVOKE ALL ON FUNCTION finvest_kyc_checks_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_checks_guard BEFORE UPDATE ON kyc_checks "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_checks_guard()",
    "REVOKE ALL ON kyc_checks, kyc_photos FROM PUBLIC",
    # No DELETE for anyone: identity records are kept for the regulator's retention period.
    f"GRANT SELECT, INSERT, UPDATE ON kyc_checks TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE ON kyc_checks TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (replaced_at) ON kyc_photos TO {APP_ROLE}",
    f"GRANT SELECT ON kyc_photos TO {WORKER_ROLE}",
    "ALTER TABLE kyc_checks ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE kyc_photos ENABLE ROW LEVEL SECURITY",
    # A client reaches their own check. Staff reach every check; which staff may is a
    # permission checked in the application.
    f"CREATE POLICY kyc_checks_app_read ON kyc_checks FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY kyc_checks_app_insert ON kyc_checks FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() AND status = 'draft')",
    f"CREATE POLICY kyc_checks_app_update ON kyc_checks FOR UPDATE TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY kyc_checks_worker ON kyc_checks TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
    # The subquery runs under the kyc_checks policies above, so a client sees photos of their own
    # check and staff see all. Only the client adds or replaces photos.
    f"CREATE POLICY kyc_photos_app_read ON kyc_photos FOR SELECT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "USING (check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_photos_app_insert ON kyc_photos FOR INSERT TO {APP_ROLE} WITH CHECK ("  # noqa: S608 - constant role name
    "finvest_actor_kind() = 'client' AND check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_photos_app_update ON kyc_photos FOR UPDATE TO {APP_ROLE} USING ("  # noqa: S608 - constant role name
    "finvest_actor_kind() = 'client' AND check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_photos_worker ON kyc_photos FOR SELECT TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), TRANSITIONS, PHOTOS_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_checks) THEN
        RAISE EXCEPTION 'refusing to drop identity checks that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Identity records are kept for the regulator; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE kyc_photos")
    op.execute("DROP TABLE kyc_checks")
    op.execute("DROP FUNCTION finvest_kyc_photos_guard()")
    op.execute("DROP FUNCTION finvest_kyc_checks_guard()")
