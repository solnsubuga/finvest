"""A second member of staff confirms every approval and rejection.

Revision ID: 0052_kyc_two_person_decisions
Revises: 0080_dealing_orders

One person proposes approving or rejecting a check, with a reason, and a different person
confirms it. The proposal is kept on the check, so the approved or rejected row names both. The
trigger enforces it: staff cannot approve or reject without an open proposal of that kind made
by someone else, and cannot change what was proposed while confirming it. Asking for new photos
and escalating stay one person's call. The worker's automatic approval, switched off until the
owner allows it, involves no person and is unchanged. See docs/identity-checks.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0052_kyc_two_person_decisions"
down_revision: str | None = "0080_dealing_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKER_ROLE = "finvest_worker"

COLUMNS = """
ALTER TABLE kyc_checks
    ADD COLUMN proposed_decision varchar(10),
    ADD COLUMN proposed_by push_id,
    ADD COLUMN proposed_at timestamptz,
    ADD COLUMN proposed_reason varchar(500),
    ADD COLUMN proposed_client_message varchar(300),
    ADD CONSTRAINT ck_kyc_checks_proposed_decision
        CHECK (proposed_decision IN ('approve', 'reject')),
    -- A proposal is whole or absent.
    ADD CONSTRAINT ck_kyc_checks_proposal CHECK (
        (proposed_decision IS NULL) = (proposed_by IS NULL)
        AND (proposed_by IS NULL) = (proposed_at IS NULL)
        AND (proposed_by IS NULL) = (proposed_reason IS NULL)
        AND (proposed_client_message IS NULL OR proposed_by IS NOT NULL)),
    -- Open only while staff are deciding, and kept on the decision it led to.
    ADD CONSTRAINT ck_kyc_checks_proposal_status CHECK (
        proposed_by IS NULL OR status IN ('needs_review', 'with_compliance', 'approved', 'rejected')),
    -- A person's decision always had two people: whoever proposed it and someone else.
    ADD CONSTRAINT ck_kyc_checks_two_people CHECK (
        status NOT IN ('approved', 'rejected') OR decided_by IS NULL OR (
            proposed_by IS NOT NULL AND proposed_by <> decided_by
            AND proposed_decision = CASE status WHEN 'approved' THEN 'approve' ELSE 'reject' END))
"""

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
        ELSIF actor = 'staff' AND NEW.status = OLD.status THEN
            -- One person proposes approving or rejecting, or a proposal is withdrawn or declined.
            -- Nothing else about a check changes in place.
            allowed := OLD.status IN ('needs_review', 'with_compliance')
                AND changed <@ ARRAY[
                    'proposed_decision', 'proposed_by', 'proposed_at', 'proposed_reason',
                    'proposed_client_message', 'updated_at']
                AND ((OLD.proposed_by IS NULL AND NEW.proposed_by = finvest_actor_id())
                    OR (OLD.proposed_by IS NOT NULL AND NEW.proposed_by IS NULL));
        ELSIF actor = 'staff' AND NEW.status IN ('approved', 'rejected') THEN
            -- A second person confirms what the first proposed, exactly as proposed.
            allowed := OLD.status IN ('needs_review', 'with_compliance')
                AND OLD.proposed_decision = CASE NEW.status
                    WHEN 'approved' THEN 'approve' ELSE 'reject' END
                AND OLD.proposed_by <> finvest_actor_id()
                AND NEW.decided_by = finvest_actor_id()
                AND NEW.client_message IS NOT DISTINCT FROM OLD.proposed_client_message
                AND changed <@ ARRAY[
                    'status', 'client_message', 'decided_at', 'decided_by', 'account_number',
                    'updated_at'];
        ELSIF actor = 'staff' THEN
            -- Asking for photos or escalating is one person's call, with no proposal open.
            allowed := ((OLD.status = 'needs_review'
                    AND NEW.status IN ('awaiting_client', 'with_compliance'))
                OR (OLD.status = 'with_compliance' AND NEW.status = 'awaiting_client'))
                AND OLD.proposed_by IS NULL
                AND changed <@ ARRAY[
                    'status', 'retake_photos', 'retake_requested_at', 'client_message',
                    'updated_at'];
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
        -- A comparison with a missing value leaves `allowed` null, which refuses too.
        IF NOT coalesce(allowed, false) THEN
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

GUARD_0051 = f"""
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

EMPTY_PROPOSALS = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_checks WHERE proposed_by IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the proposals behind identity decisions';
    END IF;
END
$$
"""

DROP_COLUMNS = """
ALTER TABLE kyc_checks
    DROP CONSTRAINT ck_kyc_checks_two_people,
    DROP CONSTRAINT ck_kyc_checks_proposal_status,
    DROP CONSTRAINT ck_kyc_checks_proposal,
    DROP CONSTRAINT ck_kyc_checks_proposed_decision,
    DROP COLUMN proposed_client_message,
    DROP COLUMN proposed_reason,
    DROP COLUMN proposed_at,
    DROP COLUMN proposed_by,
    DROP COLUMN proposed_decision
"""


def upgrade() -> None:
    op.execute(COLUMNS)
    op.execute(GUARD)


def downgrade() -> None:
    # Who proposed each decision is part of the record; a downgrade never erases it.
    op.execute(EMPTY_PROPOSALS)
    op.execute(GUARD_0051)
    op.execute(DROP_COLUMNS)
