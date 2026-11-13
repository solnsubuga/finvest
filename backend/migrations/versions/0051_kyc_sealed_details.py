"""Seal identity details and narrow what each actor may write on a check.

Revision ID: 0051_kyc_sealed_details
Revises: 0014_ledger_closed_days

The document number, date of birth and TIN are stored sealed (app/infrastructure/sealing.py), so
the database and its backups hold no readable copy. A keyed fingerprint of the document replaces
the plain columns in the "one approved client per identity document" index. The names and the
expiry date stay readable: staff search the queue by name and the expiry is checked again at
approval.

The trigger now also names the columns each actor may change, so a client cannot write the
provider's answer, and staff and the worker cannot alter what the client confirmed.
See docs/identity-checks.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0051_kyc_sealed_details"
down_revision: str | None = "0014_ledger_closed_days"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# It drops the plain detail columns, but it merged before the first deployment, so no earlier
# release can be running beside it (app/release_preflight.py).
zero_downtime = True

WORKER_ROLE = "finvest_worker"

# Sealing needs the key, which the database never holds, so saved details cannot be carried over.
UNSEALED_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_checks WHERE document_number IS NOT NULL
               OR date_of_birth IS NOT NULL OR tin IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop identity details that were saved unsealed';
    END IF;
END
$$
"""

# Version byte, nonce and tag are 29 bytes; the longest value sealed here is 32 characters.
SEALED = "octet_length({column}) BETWEEN 30 AND 128"

COLUMNS = f"""
DROP INDEX uq_kyc_checks_approved_document;
ALTER TABLE kyc_checks
    DROP CONSTRAINT ck_kyc_checks_details,
    DROP CONSTRAINT ck_kyc_checks_document_number,
    DROP CONSTRAINT ck_kyc_checks_tin,
    DROP COLUMN document_number,
    DROP COLUMN date_of_birth,
    DROP COLUMN tin,
    ADD COLUMN document_number_sealed bytea,
    ADD COLUMN date_of_birth_sealed bytea,
    ADD COLUMN tin_sealed bytea,
    -- HMAC of country, type and number. Equal documents match without the number being stored.
    ADD COLUMN document_fingerprint char(64) COLLATE "C",
    ADD CONSTRAINT ck_kyc_checks_document_number_sealed
        CHECK ({SEALED.format(column="document_number_sealed")}),
    ADD CONSTRAINT ck_kyc_checks_date_of_birth_sealed
        CHECK ({SEALED.format(column="date_of_birth_sealed")}),
    ADD CONSTRAINT ck_kyc_checks_tin_sealed CHECK ({SEALED.format(column="tin_sealed")}),
    ADD CONSTRAINT ck_kyc_checks_document_fingerprint
        CHECK (document_fingerprint ~ '^[0-9a-f]{{64}}$'),
    -- Nothing leaves draft without confirmed details.
    ADD CONSTRAINT ck_kyc_checks_details CHECK (
        status = 'draft' OR (
            details_confirmed_at IS NOT NULL AND surname IS NOT NULL AND given_names IS NOT NULL
            AND document_number_sealed IS NOT NULL AND date_of_birth_sealed IS NOT NULL
            AND document_fingerprint IS NOT NULL AND document_expires_on IS NOT NULL
            AND submitted_at IS NOT NULL AND submission > 0)),
    -- Identity checks open personal accounts, whose numbers start with 10.
    ADD CONSTRAINT ck_kyc_checks_account_number_type CHECK (account_number LIKE '10%');
-- One approved client per identity document.
CREATE UNIQUE INDEX uq_kyc_checks_approved_document
    ON kyc_checks (document_fingerprint) WHERE status = 'approved'
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001), so the
# system's writes also require the worker's database role.
GUARD = f"""
CREATE OR REPLACE FUNCTION finvest_kyc_checks_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        before jsonb := to_jsonb(OLD);
        changed text[];
        allowed boolean;
    BEGIN
        IF OLD.status IN ('approved', 'rejected') THEN
            RAISE EXCEPTION 'a decided identity check never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'an identity check cannot move to another client' USING ERRCODE = '42501';
        END IF;
        SELECT coalesce(array_agg(key), '{{}}') INTO changed FROM jsonb_each(to_jsonb(NEW))
            WHERE value IS DISTINCT FROM before -> key;
        IF actor = 'client' AND NEW.status = OLD.status THEN
            -- A draft is the client's: the document they chose and the details they confirmed.
            allowed := OLD.status = 'draft' AND changed <@ ARRAY[
                'document_type', 'document_country', 'surname', 'given_names',
                'document_number_sealed', 'date_of_birth_sealed', 'document_expires_on',
                'tin_sealed', 'document_fingerprint', 'details_confirmed_at', 'updated_at'];
        ELSIF actor = 'client' THEN
            -- Handing in starts a new submission with nothing from a provider on it.
            allowed := OLD.status IN ('draft', 'awaiting_client')
                AND NEW.status IN ('submitted', 'needs_review')
                AND NEW.submission = OLD.submission + 1
                AND NEW.provider_reference IS NULL AND NEW.automatic_checks = '[]'::jsonb
                AND changed <@ ARRAY[
                    'status', 'submission', 'submitted_at', 'provider', 'provider_reference',
                    'automatic_checks', 'review_flags', 'retake_photos', 'retake_requested_at',
                    'client_message', 'updated_at'];
        ELSIF actor = 'staff' THEN
            -- Staff decide. They never edit a check in place or alter what the client confirmed.
            allowed := ((OLD.status = 'needs_review'
                    AND NEW.status IN ('approved', 'rejected', 'awaiting_client', 'with_compliance'))
                OR (OLD.status = 'with_compliance'
                    AND NEW.status IN ('approved', 'rejected', 'awaiting_client')))
                AND changed <@ ARRAY[
                    'status', 'retake_photos', 'retake_requested_at', 'client_message',
                    'decided_at', 'decided_by', 'account_number', 'updated_at'];
        ELSIF actor = 'system' AND current_user = '{WORKER_ROLE}' AND NEW.status = OLD.status THEN
            -- The worker notes the provider's reference while the answer is awaited.
            allowed := OLD.status = 'submitted'
                AND changed <@ ARRAY['provider_reference', 'updated_at'];
        ELSIF actor = 'system' AND current_user = '{WORKER_ROLE}' THEN
            allowed := OLD.status = 'submitted' AND NEW.status IN ('approved', 'needs_review')
                AND changed <@ ARRAY[
                    'status', 'provider_reference', 'automatic_checks', 'review_flags',
                    'decided_at', 'account_number', 'updated_at'];
        ELSE
            allowed := false;
        END IF;
        IF NOT allowed THEN
            IF NEW.status = OLD.status THEN
                RAISE EXCEPTION 'this change to an identity check is not allowed here'
                    USING ERRCODE = '42501';
            END IF;
            RAISE EXCEPTION 'identity check cannot go from % to % here', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [UNSEALED_GUARD, *split(COLUMNS), GUARD]:
        op.execute(statement)


# Without the key the sealed details cannot be turned back into plain columns.
SEALED_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_checks WHERE document_number_sealed IS NOT NULL
               OR date_of_birth_sealed IS NOT NULL OR tin_sealed IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop sealed identity details';
    END IF;
END
$$
"""

PLAIN_COLUMNS = """
DROP INDEX uq_kyc_checks_approved_document;
ALTER TABLE kyc_checks
    DROP CONSTRAINT ck_kyc_checks_account_number_type,
    DROP CONSTRAINT ck_kyc_checks_details,
    DROP COLUMN document_number_sealed,
    DROP COLUMN date_of_birth_sealed,
    DROP COLUMN tin_sealed,
    DROP COLUMN document_fingerprint,
    ADD COLUMN document_number varchar(32) COLLATE "C",
    ADD COLUMN date_of_birth date,
    ADD COLUMN tin varchar(10) COLLATE "C",
    ADD CONSTRAINT ck_kyc_checks_document_number CHECK (document_number ~ '^[A-Z0-9]{5,32}$'),
    ADD CONSTRAINT ck_kyc_checks_tin CHECK (tin ~ '^[0-9]{10}$'),
    ADD CONSTRAINT ck_kyc_checks_details CHECK (
        status = 'draft' OR (
            details_confirmed_at IS NOT NULL AND surname IS NOT NULL AND given_names IS NOT NULL
            AND document_number IS NOT NULL AND date_of_birth IS NOT NULL
            AND document_expires_on IS NOT NULL AND submitted_at IS NOT NULL AND submission > 0));
CREATE UNIQUE INDEX uq_kyc_checks_approved_document
    ON kyc_checks (document_country, document_type, document_number) WHERE status = 'approved'
"""

# The guard exactly as migration 0050 created it.
GUARD_0050 = f"""
CREATE OR REPLACE FUNCTION finvest_kyc_checks_guard() RETURNS trigger
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


def downgrade() -> None:
    op.execute(SEALED_GUARD)
    for statement in [*split(PLAIN_COLUMNS), GUARD_0050]:
        op.execute(statement)
