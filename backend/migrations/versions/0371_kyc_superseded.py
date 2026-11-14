"""Send a client carried over from Finvest 1.0 to the full ID check when their claim photos fail.

Revision ID: 0371_kyc_superseded
Revises: 0321_finance_month_closes

A client carried over from Finvest 1.0 takes a selfie and a photo of their document at the claim,
and staff compare them with the check carried over (migration 0053). When staff say they do not
match, the client does the full ID check, as a new client would (the owner's decision). See
docs/identity-checks.md#photos-that-do-not-match.

* In the transaction where staff decide the claim check is not matched, the carried-over check
  moves from approved to `superseded`, a final state. It records when, who, and the claim check
  that decided it. It is the one step a decided check ever takes, and only a carried-over check
  takes it.
* A client has one live check: any number of superseded ones and at most one other. So the
  client can start a new draft beside the superseded check.
* The personal account number stays with the client. The superseded check keeps it, and when
  the new check is approved it is given the same number again, never another client's.

The constraints that were one per client become partial unique indexes, which the running
release's `ON CONFLICT ON CONSTRAINT` cannot use, so it is stopped first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0371_kyc_superseded"
down_revision: str | None = "0321_finance_month_closes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# The previous release inserts drafts with ON CONFLICT ON CONSTRAINT uq_kyc_checks_client.
zero_downtime = False

WORKER_ROLE = "finvest_worker"

COLUMNS = """
ALTER TABLE kyc_checks
    ADD COLUMN superseded_at timestamptz,
    ADD COLUMN superseded_by push_id,
    -- The claim check whose photos did not match.
    ADD COLUMN superseded_by_claim push_id,
    ADD CONSTRAINT fk_kyc_checks_superseded_by_claim
        FOREIGN KEY (superseded_by_claim) REFERENCES kyc_claim_checks (id),
    ADD CONSTRAINT ck_kyc_checks_superseded CHECK (
        (status = 'superseded') = (superseded_at IS NOT NULL)
        AND (superseded_at IS NULL) = (superseded_by IS NULL)
        AND (superseded_at IS NULL) = (superseded_by_claim IS NULL)
        AND (status <> 'superseded' OR source = 'finvest1')),
    DROP CONSTRAINT ck_kyc_checks_status,
    ADD CONSTRAINT ck_kyc_checks_status CHECK (status IN (
        'draft', 'submitted', 'needs_review', 'with_compliance', 'awaiting_client',
        'approved', 'rejected', 'superseded')),
    DROP CONSTRAINT ck_kyc_checks_decided,
    ADD CONSTRAINT ck_kyc_checks_decided CHECK (
        (status IN ('approved', 'rejected', 'superseded')) = (decided_at IS NOT NULL)),
    -- Approved clients hold an account number, and a superseded check keeps the one it had.
    DROP CONSTRAINT ck_kyc_checks_account_number,
    ADD CONSTRAINT ck_kyc_checks_account_number CHECK (
        (status IN ('approved', 'superseded')) = (account_number IS NOT NULL)),
    DROP CONSTRAINT ck_kyc_checks_carried_over,
    ADD CONSTRAINT ck_kyc_checks_carried_over CHECK (
        source = 'finvest2' OR (
            status IN ('approved', 'superseded') AND submission = 0 AND submitted_at IS NULL
            AND provider IS NULL AND provider_reference IS NULL
            AND automatic_checks = '[]'::jsonb)),
    DROP CONSTRAINT uq_kyc_checks_client,
    DROP CONSTRAINT uq_kyc_checks_account_number;
-- One live check per client; superseded ones stay beside it.
CREATE UNIQUE INDEX uq_kyc_checks_client ON kyc_checks (client_id)
    WHERE status <> 'superseded';
-- A superseded check and the client's new approved check hold the same number.
CREATE UNIQUE INDEX uq_kyc_checks_account_number ON kyc_checks (account_number)
    WHERE status <> 'superseded'
"""

# As in migration 0051, with the one step a decided check takes, and the rule that an account
# number given again is the same client's.
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
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'an identity check cannot move to another client' USING ERRCODE = '42501';
        END IF;
        SELECT coalesce(array_agg(key), '{{}}') INTO changed FROM jsonb_each(to_jsonb(NEW))
            WHERE value IS DISTINCT FROM before -> key;
        IF OLD.status = 'approved' AND NEW.status = 'superseded' THEN
            -- Staff found the claim photos did not match the check carried over, in this
            -- transaction. Nothing the check says about the client changes.
            IF NOT (actor = 'staff' AND OLD.source = 'finvest1'
                    AND NEW.superseded_by = finvest_actor_id()
                    AND changed <@ ARRAY[
                        'status', 'superseded_at', 'superseded_by', 'superseded_by_claim',
                        'updated_at']
                    AND EXISTS (SELECT FROM kyc_claim_checks AS claim
                                WHERE claim.id = NEW.superseded_by_claim
                                  AND claim.check_id = OLD.id
                                  AND claim.status = 'not_matched')) THEN
                RAISE EXCEPTION 'only a carried-over check whose claim photos did not match is superseded'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('approved', 'rejected', 'superseded') THEN
            RAISE EXCEPTION 'a decided identity check never changes' USING ERRCODE = '42501';
        END IF;
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
        -- A number held by a superseded check is given again only to the same client.
        IF NEW.status = 'approved' AND EXISTS (
                SELECT FROM kyc_checks AS other
                 WHERE other.account_number = NEW.account_number AND other.id <> NEW.id
                   AND other.client_id <> NEW.client_id) THEN
            RAISE EXCEPTION 'an account number belongs to one client' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

# Migration 0051's guard, with the actor branches unchanged, for a downgrade.
PREVIOUS_GUARD = f"""
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
            allowed := OLD.status = 'draft' AND changed <@ ARRAY[
                'document_type', 'document_country', 'surname', 'given_names',
                'document_number_sealed', 'date_of_birth_sealed', 'document_expires_on',
                'tin_sealed', 'document_fingerprint', 'details_confirmed_at', 'updated_at'];
        ELSIF actor = 'client' THEN
            allowed := OLD.status IN ('draft', 'awaiting_client')
                AND NEW.status IN ('submitted', 'needs_review')
                AND NEW.submission = OLD.submission + 1
                AND NEW.provider_reference IS NULL AND NEW.automatic_checks = '[]'::jsonb
                AND changed <@ ARRAY[
                    'status', 'submission', 'submitted_at', 'provider', 'provider_reference',
                    'automatic_checks', 'review_flags', 'retake_photos', 'retake_requested_at',
                    'client_message', 'updated_at'];
        ELSIF actor = 'staff' THEN
            allowed := ((OLD.status = 'needs_review'
                    AND NEW.status IN ('approved', 'rejected', 'awaiting_client', 'with_compliance'))
                OR (OLD.status = 'with_compliance'
                    AND NEW.status IN ('approved', 'rejected', 'awaiting_client')))
                AND changed <@ ARRAY[
                    'status', 'retake_photos', 'retake_requested_at', 'client_message',
                    'decided_at', 'decided_by', 'account_number', 'updated_at'];
        ELSIF actor = 'system' AND current_user = '{WORKER_ROLE}' AND NEW.status = OLD.status THEN
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
    for statement in [*split(COLUMNS), GUARD]:
        op.execute(statement)


# Identity records are kept for the regulator; a downgrade never erases them.
SUPERSEDED_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_checks WHERE superseded_at IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop superseded identity checks';
    END IF;
END
$$
"""

PREVIOUS_COLUMNS = """
DROP INDEX uq_kyc_checks_account_number;
DROP INDEX uq_kyc_checks_client;
ALTER TABLE kyc_checks
    ADD CONSTRAINT uq_kyc_checks_client UNIQUE (client_id),
    ADD CONSTRAINT uq_kyc_checks_account_number UNIQUE (account_number),
    DROP CONSTRAINT ck_kyc_checks_carried_over,
    ADD CONSTRAINT ck_kyc_checks_carried_over CHECK (
        source = 'finvest2' OR (
            status = 'approved' AND submission = 0 AND submitted_at IS NULL AND provider IS NULL
            AND provider_reference IS NULL AND automatic_checks = '[]'::jsonb)),
    DROP CONSTRAINT ck_kyc_checks_account_number,
    ADD CONSTRAINT ck_kyc_checks_account_number CHECK (
        (status = 'approved') = (account_number IS NOT NULL)),
    DROP CONSTRAINT ck_kyc_checks_decided,
    ADD CONSTRAINT ck_kyc_checks_decided CHECK (
        (status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)),
    DROP CONSTRAINT ck_kyc_checks_status,
    ADD CONSTRAINT ck_kyc_checks_status CHECK (status IN (
        'draft', 'submitted', 'needs_review', 'with_compliance', 'awaiting_client',
        'approved', 'rejected')),
    DROP CONSTRAINT ck_kyc_checks_superseded,
    DROP CONSTRAINT fk_kyc_checks_superseded_by_claim,
    DROP COLUMN superseded_by_claim,
    DROP COLUMN superseded_by,
    DROP COLUMN superseded_at
"""


def downgrade() -> None:
    op.execute(SUPERSEDED_GUARD)
    for statement in [PREVIOUS_GUARD, *split(PREVIOUS_COLUMNS)]:
        op.execute(statement)
