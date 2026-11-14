"""Company and group accounts: the application, its signatories and its documents.

Revision ID: 0242_organisations
Revises: 0241_investment_transfers

* `organisations` holds each application for a company or group account. An approved personal
  client applies and may change it while it is a draft. One member of staff checks it, and a
  different one approves it, which gives it an account number. Staff may also send it back to
  the applicant to change, or reject it. PostgreSQL enforces who makes each move, that the
  approver is never the checker, and that an approved or rejected application never changes.
* `organisation_signatories` holds the people who sign for it. The applicant adds them and
  they answer for themselves. Their shown name and account number are copied in when they are
  added, so no client ever reads another client's identity check.
* `organisation_documents` holds one current file of each kind the application needs. The file
  itself is in the storage bucket under the key recorded here.
* `finvest_organisation_member` is a SECURITY DEFINER function, so the row policies of the
  three tables can ask who belongs to an organisation without reading each other.

See docs/organisations.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0242_organisations"
down_revision: str | None = "0241_investment_transfers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
NOTE = "btrim({column}) <> '' AND length({column}) <= 500"

TABLES = f"""
CREATE TABLE organisations (
    id push_id NOT NULL,
    kind text NOT NULL,
    name text NOT NULL,
    registration_number text,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    applicant_id push_id NOT NULL,
    approvals_needed smallint NOT NULL,
    status text NOT NULL DEFAULT 'draft',
    created_at timestamptz NOT NULL,
    submitted_at timestamptz,
    checked_by push_id,
    checked_at timestamptz,
    check_note text,
    returned_by push_id,
    returned_at timestamptz,
    return_note text,
    decided_by push_id,
    decided_at timestamptz,
    decision_note text,
    account_number char(10),
    CONSTRAINT pk_organisations PRIMARY KEY (id),
    CONSTRAINT ck_organisations_kind CHECK (kind IN ('company', 'group')),
    CONSTRAINT ck_organisations_status
        CHECK (status IN ('draft', 'submitted', 'checked', 'approved', 'rejected')),
    CONSTRAINT ck_organisations_name CHECK (btrim(name) <> '' AND length(name) <= 120),
    CONSTRAINT ck_organisations_registration CHECK (
        registration_number IS NULL OR registration_number ~ '^[A-Z0-9][A-Z0-9/ -]{{2,38}}[A-Z0-9]$'),
    CONSTRAINT ck_organisations_approvals CHECK (approvals_needed BETWEEN 1 AND 10),
    CONSTRAINT ck_organisations_submitted CHECK (
        (status = 'draft') OR submitted_at IS NOT NULL),
    CONSTRAINT ck_organisations_checked CHECK (
        (checked_by IS NULL) = (checked_at IS NULL)
        AND (checked_by IS NULL) = (check_note IS NULL)
        AND (check_note IS NULL OR ({NOTE.format(column="check_note")}))
        AND (status NOT IN ('checked', 'approved') OR checked_by IS NOT NULL)
        AND (status <> 'draft' OR checked_by IS NULL)),
    CONSTRAINT ck_organisations_returned CHECK (
        (returned_by IS NULL) = (returned_at IS NULL)
        AND (returned_by IS NULL) = (return_note IS NULL)
        AND (return_note IS NULL OR ({NOTE.format(column="return_note")}))),
    CONSTRAINT ck_organisations_decided CHECK (
        (status IN ('approved', 'rejected')) = (decided_by IS NOT NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_note IS NULL)
        AND (decision_note IS NULL OR ({NOTE.format(column="decision_note")}))),
    CONSTRAINT ck_organisations_account_number CHECK (
        (status = 'approved') = (account_number IS NOT NULL)
        AND (account_number IS NULL OR account_number ~ (CASE kind WHEN 'company' THEN '^30' ELSE '^40' END))),
    CONSTRAINT ck_organisations_two_people CHECK (status <> 'approved' OR decided_by <> checked_by)
);
CREATE UNIQUE INDEX uq_organisations_account_number ON organisations (account_number)
    WHERE account_number IS NOT NULL;
-- One open application per registration number, so a company is not opened twice.
CREATE UNIQUE INDEX uq_organisations_registration ON organisations (kind, registration_number)
    WHERE registration_number IS NOT NULL AND status <> 'rejected';
CREATE INDEX ix_organisations_applicant ON organisations (applicant_id, created_at DESC);
CREATE INDEX ix_organisations_waiting ON organisations (submitted_at)
    WHERE status IN ('submitted', 'checked');
CREATE TABLE organisation_signatories (
    organisation_id push_id NOT NULL,
    client_id push_id NOT NULL,
    role text NOT NULL,
    -- As the other clients see them: "Brian K.", and their personal account number.
    shown_name text NOT NULL,
    account_number char(10) NOT NULL,
    is_applicant boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'invited',
    invited_at timestamptz NOT NULL,
    answered_at timestamptz,
    CONSTRAINT pk_organisation_signatories PRIMARY KEY (organisation_id, client_id),
    CONSTRAINT fk_organisation_signatories_organisation FOREIGN KEY (organisation_id)
        REFERENCES organisations (id),
    CONSTRAINT ck_organisation_signatories_status
        CHECK (status IN ('invited', 'joined', 'declined', 'removed')),
    CONSTRAINT ck_organisation_signatories_role CHECK (btrim(role) <> '' AND length(role) <= 60),
    CONSTRAINT ck_organisation_signatories_name
        CHECK (btrim(shown_name) <> '' AND length(shown_name) <= 80),
    CONSTRAINT ck_organisation_signatories_number CHECK (account_number ~ '^10[0-9]{{8}}$'),
    CONSTRAINT ck_organisation_signatories_applicant CHECK (NOT is_applicant OR status = 'joined'),
    CONSTRAINT ck_organisation_signatories_answered CHECK (
        (status IN ('joined', 'declined')) = (answered_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_organisation_signatories_applicant
    ON organisation_signatories (organisation_id) WHERE is_applicant;
CREATE INDEX ix_organisation_signatories_client ON organisation_signatories (client_id);
CREATE TABLE organisation_documents (
    organisation_id push_id NOT NULL,
    kind text NOT NULL,
    file_id push_id NOT NULL,
    storage_key text NOT NULL,
    content_type text NOT NULL,
    byte_size integer NOT NULL,
    sha256 char(64) NOT NULL,
    uploaded_by push_id NOT NULL,
    uploaded_at timestamptz NOT NULL,
    CONSTRAINT pk_organisation_documents PRIMARY KEY (organisation_id, kind),
    CONSTRAINT fk_organisation_documents_organisation FOREIGN KEY (organisation_id)
        REFERENCES organisations (id),
    CONSTRAINT ck_organisation_documents_kind CHECK (kind IN (
        'certificate_of_incorporation', 'board_resolution', 'constitution', 'group_resolution')),
    CONSTRAINT ck_organisation_documents_type
        CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    CONSTRAINT ck_organisation_documents_size CHECK (byte_size BETWEEN 1 AND 10485760),
    CONSTRAINT ck_organisation_documents_sha256 CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT ck_organisation_documents_key CHECK (storage_key ~ '^organisations/')
)
"""

# Who belongs to an organisation, asked without the row policies of the tables themselves.
MEMBER = """
CREATE FUNCTION public.finvest_organisation_member(p_organisation_id text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT public.finvest_actor_kind() = 'client' AND (
            EXISTS (SELECT FROM public.organisations
                     WHERE id = p_organisation_id AND applicant_id = public.finvest_actor_id())
            OR EXISTS (SELECT FROM public.organisation_signatories
                        WHERE organisation_id = p_organisation_id
                          AND client_id = public.finvest_actor_id()
                          AND status IN ('invited', 'joined')))
    $$
"""

# The organisation's status and applicant, for the guards of the two child tables.
STANDING = """
CREATE FUNCTION public.finvest_organisation_standing(p_organisation_id text)
    RETURNS TABLE (status text, applicant_id text, kind text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT status, applicant_id::text, kind FROM public.organisations
         WHERE id = p_organisation_id
    $$
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
GUARD = """
CREATE FUNCTION finvest_organisations_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_kind text := public.finvest_actor_kind();
        v_actor text := public.finvest_actor_id();
        v_staff boolean := v_kind = 'staff' AND EXISTS (
            SELECT FROM public.identity_staff WHERE id = v_actor AND status = 'active');
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF v_kind IS DISTINCT FROM 'client' OR NEW.applicant_id IS DISTINCT FROM v_actor
               OR NEW.status <> 'draft' OR NEW.submitted_at IS NOT NULL
               OR NEW.checked_by IS NOT NULL OR NEW.returned_by IS NOT NULL
               OR NEW.decided_by IS NOT NULL THEN
                RAISE EXCEPTION 'a client applies for an organisation as themselves, as a draft'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('approved', 'rejected') THEN
            RAISE EXCEPTION 'an organisation approved or rejected never changes'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.kind, NEW.applicant_id, NEW.created_at)
           IS DISTINCT FROM (OLD.id, OLD.kind, OLD.applicant_id, OLD.created_at) THEN
            RAISE EXCEPTION 'who applied, and for what kind, never changes'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'draft' THEN
            IF v_kind IS DISTINCT FROM 'client' OR OLD.applicant_id IS DISTINCT FROM v_actor THEN
                RAISE EXCEPTION 'only the applicant changes or hands in a draft'
                    USING ERRCODE = '42501';
            END IF;
            IF (NEW.checked_by, NEW.returned_by, NEW.returned_at, NEW.return_note,
                NEW.decided_by, NEW.account_number)
               IS DISTINCT FROM (OLD.checked_by, OLD.returned_by, OLD.returned_at,
                OLD.return_note, OLD.decided_by, OLD.account_number) THEN
                RAISE EXCEPTION 'staff fields are for staff' USING ERRCODE = '42501';
            END IF;
            IF NEW.status = 'draft' THEN
                RETURN NEW;
            END IF;
            IF NEW.status <> 'submitted' OR NEW.submitted_at IS NULL THEN
                RAISE EXCEPTION 'a draft is only handed in' USING ERRCODE = '42501';
            END IF;
            IF EXISTS (SELECT FROM public.organisation_signatories
                        WHERE organisation_id = NEW.id AND status = 'invited')
               OR (SELECT count(*) FROM public.organisation_signatories
                    WHERE organisation_id = NEW.id AND status = 'joined') < NEW.approvals_needed
               OR (SELECT count(*) FROM public.organisation_documents
                    WHERE organisation_id = NEW.id
                      AND kind = ANY (CASE NEW.kind
                        WHEN 'company' THEN ARRAY['certificate_of_incorporation', 'board_resolution']
                        ELSE ARRAY['constitution', 'group_resolution'] END)) < 2 THEN
                RAISE EXCEPTION 'an application is handed in with every signatory and document'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        -- From here the application waits for staff, and what the client gave never changes.
        IF NOT v_staff THEN
            RAISE EXCEPTION 'only active staff check, approve, return or reject an application'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.name, NEW.registration_number, NEW.approvals_needed, NEW.submitted_at)
           IS DISTINCT FROM (OLD.name, OLD.registration_number, OLD.approvals_needed,
            OLD.submitted_at) THEN
            RAISE EXCEPTION 'what the client handed in never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'checked' THEN
            IF OLD.status <> 'submitted' OR NEW.checked_by IS DISTINCT FROM v_actor
               OR NEW.decided_by IS NOT NULL OR NEW.account_number IS NOT NULL THEN
                RAISE EXCEPTION 'a handed in application is checked once, by the acting staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.status = 'draft' THEN
            IF NEW.returned_by IS DISTINCT FROM v_actor OR NEW.checked_by IS NOT NULL
               OR NEW.decided_by IS NOT NULL OR NEW.account_number IS NOT NULL THEN
                RAISE EXCEPTION 'an application is sent back by the acting staff, unchecked'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.checked_by, NEW.checked_at, NEW.check_note)
           IS DISTINCT FROM (OLD.checked_by, OLD.checked_at, OLD.check_note) THEN
            RAISE EXCEPTION 'the check never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' THEN
            IF OLD.status <> 'checked' OR NEW.decided_by IS DISTINCT FROM v_actor
               OR NEW.decided_by = OLD.checked_by THEN
                RAISE EXCEPTION 'a checked application is approved by a second member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.status = 'rejected' AND NEW.decided_by IS NOT DISTINCT FROM v_actor
           AND NEW.account_number IS NULL THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'that is not a move an application makes' USING ERRCODE = '42501';
    END;
    $$
"""

SIGNATORIES_GUARD = """
CREATE FUNCTION finvest_organisation_signatories_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_kind text := public.finvest_actor_kind();
        v_actor text := public.finvest_actor_id();
        v_status text;
        v_applicant text;
    BEGIN
        SELECT s.status, s.applicant_id INTO v_status, v_applicant
          FROM public.finvest_organisation_standing(NEW.organisation_id) AS s;
        IF v_status IS DISTINCT FROM 'draft' OR v_kind IS DISTINCT FROM 'client' THEN
            RAISE EXCEPTION 'signatories change only on a draft, by its clients'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF v_applicant IS DISTINCT FROM v_actor
               OR (NEW.is_applicant AND NEW.client_id IS DISTINCT FROM v_actor)
               OR (NOT NEW.is_applicant AND (NEW.client_id = v_actor
                   OR NEW.status <> 'invited')) THEN
                RAISE EXCEPTION 'the applicant joins first and invites the others'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.organisation_id, NEW.client_id, NEW.is_applicant)
           IS DISTINCT FROM (OLD.organisation_id, OLD.client_id, OLD.is_applicant)
           OR OLD.is_applicant THEN
            RAISE EXCEPTION 'who a signatory is never changes, and the applicant stays'
                USING ERRCODE = '42501';
        END IF;
        -- The person answers for themselves.
        IF v_actor = OLD.client_id THEN
            IF OLD.status = 'invited' AND NEW.status IN ('joined', 'declined')
               AND (NEW.role, NEW.shown_name, NEW.account_number, NEW.invited_at)
                   IS NOT DISTINCT FROM (OLD.role, OLD.shown_name, OLD.account_number,
                    OLD.invited_at) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'a signatory only joins or declines' USING ERRCODE = '42501';
        END IF;
        -- The applicant removes someone, or asks someone who declined or was removed again.
        IF v_actor = v_applicant AND (
            (NEW.status = 'removed' AND OLD.status <> 'removed')
            OR (NEW.status = 'invited' AND OLD.status IN ('declined', 'removed')
                AND NEW.answered_at IS NULL)) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'that is not a move a signatory makes' USING ERRCODE = '42501';
    END;
    $$
"""

DOCUMENTS_GUARD = """
CREATE FUNCTION finvest_organisation_documents_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_status text;
        v_applicant text;
        v_org_kind text;
    BEGIN
        SELECT s.status, s.applicant_id, s.kind INTO v_status, v_applicant, v_org_kind
          FROM public.finvest_organisation_standing(NEW.organisation_id) AS s;
        IF v_status IS DISTINCT FROM 'draft'
           OR public.finvest_actor_kind() IS DISTINCT FROM 'client'
           OR v_applicant IS DISTINCT FROM public.finvest_actor_id()
           OR NEW.uploaded_by IS DISTINCT FROM v_applicant THEN
            RAISE EXCEPTION 'only the applicant adds documents, to a draft'
                USING ERRCODE = '42501';
        END IF;
        IF NOT (CASE v_org_kind
                WHEN 'company' THEN NEW.kind IN ('certificate_of_incorporation', 'board_resolution')
                ELSE NEW.kind IN ('constitution', 'group_resolution') END) THEN
            RAISE EXCEPTION 'that document is for another kind of organisation'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'UPDATE' AND (NEW.organisation_id, NEW.kind)
           IS DISTINCT FROM (OLD.organisation_id, OLD.kind) THEN
            RAISE EXCEPTION 'a document replaces one of its own kind' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

READ = "finvest_actor_kind() = 'staff' OR finvest_organisation_member({column})"

RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_organisation_member(text) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.finvest_organisation_member(text) TO {APP_ROLE}",
    "REVOKE ALL ON FUNCTION public.finvest_organisation_standing(text) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.finvest_organisation_standing(text) TO {APP_ROLE}",
    "REVOKE ALL ON FUNCTION finvest_organisations_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_organisation_signatories_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_organisation_documents_guard() FROM PUBLIC",
    "CREATE TRIGGER organisations_guard BEFORE INSERT OR UPDATE ON organisations "
    "FOR EACH ROW EXECUTE FUNCTION finvest_organisations_guard()",
    "CREATE TRIGGER organisation_signatories_guard BEFORE INSERT OR UPDATE "
    "ON organisation_signatories "
    "FOR EACH ROW EXECUTE FUNCTION finvest_organisation_signatories_guard()",
    "CREATE TRIGGER organisation_documents_guard BEFORE INSERT OR UPDATE "
    "ON organisation_documents "
    "FOR EACH ROW EXECUTE FUNCTION finvest_organisation_documents_guard()",
    # No DELETE for anyone: who applied, who signs and what they handed in is part of the record.
    "REVOKE ALL ON organisations, organisation_signatories, organisation_documents FROM PUBLIC",
    f"GRANT SELECT, INSERT ON organisations TO {APP_ROLE}",
    "GRANT UPDATE (name, registration_number, approvals_needed, status, submitted_at, "
    "checked_by, checked_at, check_note, returned_by, returned_at, return_note, decided_by, "
    f"decided_at, decision_note, account_number) ON organisations TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON organisation_signatories TO {APP_ROLE}",
    f"GRANT UPDATE (role, status, answered_at, invited_at) ON organisation_signatories TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON organisation_documents TO {APP_ROLE}",
    "GRANT UPDATE (file_id, storage_key, content_type, byte_size, sha256, uploaded_by, "
    f"uploaded_at) ON organisation_documents TO {APP_ROLE}",
    # The worker reads whether an invitation still stands before it tells the person.
    f"GRANT SELECT ON organisations, organisation_signatories TO {WORKER_ROLE}",
    "ALTER TABLE organisations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE organisation_signatories ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE organisation_documents ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY organisations_read ON organisations FOR SELECT TO {APP_ROLE} "
    f"USING ({READ.format(column='id')})",
    f"CREATE POLICY organisations_insert ON organisations FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND applicant_id = finvest_actor_id())",
    f"CREATE POLICY organisations_update ON organisations FOR UPDATE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff' "
    "OR (finvest_actor_kind() = 'client' AND applicant_id = finvest_actor_id()))",
    "CREATE POLICY organisation_signatories_read ON organisation_signatories FOR SELECT "
    f"TO {APP_ROLE} USING ({READ.format(column='organisation_id')})",
    "CREATE POLICY organisation_signatories_insert ON organisation_signatories FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'client')",
    "CREATE POLICY organisation_signatories_update ON organisation_signatories FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_organisation_member(organisation_id)) "
    "WITH CHECK (finvest_actor_kind() = 'client')",
    "CREATE POLICY organisation_documents_read ON organisation_documents FOR SELECT "
    f"TO {APP_ROLE} USING ({READ.format(column='organisation_id')})",
    "CREATE POLICY organisation_documents_insert ON organisation_documents FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'client')",
    "CREATE POLICY organisation_documents_update ON organisation_documents FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_organisation_member(organisation_id)) "
    "WITH CHECK (finvest_actor_kind() = 'client')",
]


RULES += [
    f"CREATE POLICY {table}_worker ON {table} FOR SELECT TO {WORKER_ROLE} USING (true)"
    for table in ("organisations", "organisation_signatories")
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(MEMBER)
    op.execute(STANDING)
    op.execute(GUARD)
    op.execute(SIGNATORIES_GUARD)
    op.execute(DOCUMENTS_GUARD)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM organisations) THEN
        RAISE EXCEPTION 'refusing to drop the record of company and group applications';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE organisation_documents")
    op.execute("DROP TABLE organisation_signatories")
    op.execute("DROP TABLE organisations")
    op.execute("DROP FUNCTION finvest_organisation_documents_guard()")
    op.execute("DROP FUNCTION finvest_organisation_signatories_guard()")
    op.execute("DROP FUNCTION finvest_organisations_guard()")
    op.execute("DROP FUNCTION public.finvest_organisation_standing(text)")
    op.execute("DROP FUNCTION public.finvest_organisation_member(text)")
