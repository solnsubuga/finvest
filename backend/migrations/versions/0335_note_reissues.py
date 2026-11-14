"""Send investment notes again, by two people.

Revision ID: 0335_note_reissues
Revises: 0321_finance_month_closes

`note_reissues` keeps each request staff make to email investment notes again: one client's, or
every client's, for the notes issued between two days. One member of staff proposes with a
reason, a different one approves or declines it, and only the proposer withdraws it. Approving
queues `documents.reissue_notes`, and the worker records one `document_email_copies` row for each
note it finds and emails that note's own stored PDF again, locked as before. A copy is the same
document, with the same number and figures: nothing is built again. See docs/documents.md.

The database refuses a decision by the proposer, any change after a decision except the worker
finishing an approved request, and a copy of anything but a ready investment note of the client,
inside the approved request's scope.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0335_note_reissues"
down_revision: str | None = "0321_finance_month_closes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"
# The most days one request covers.
MAX_DAYS = 31

REISSUES = f"""
CREATE TABLE note_reissues (
    id push_id NOT NULL,
    -- The one client whose notes are sent again, or null for every client's.
    client_id push_id,
    -- The Kampala business days the notes were issued on, both included.
    issued_from date NOT NULL,
    issued_to date NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    -- The job the approval queued, in the jobs table.
    job_id push_id,
    -- How many notes the worker sent again, once it is done.
    sent_count integer,
    finished_at timestamptz,
    CONSTRAINT pk_note_reissues PRIMARY KEY (id),
    CONSTRAINT ck_note_reissues_days CHECK (
        issued_from <= issued_to AND issued_to - issued_from < {MAX_DAYS}),
    CONSTRAINT ck_note_reissues_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_note_reissues_status CHECK (
        status IN ('proposed', 'approved', 'declined', 'withdrawn', 'done')),
    CONSTRAINT ck_note_reissues_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own request, and only the proposer withdraws it.
    CONSTRAINT ck_note_reissues_second_person CHECK (
        status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_note_reissues_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_note_reissues_note CHECK (
        status <> 'declined' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Approved means the sending was queued, and done means it finished.
    CONSTRAINT ck_note_reissues_job CHECK (
        (status IN ('approved', 'done')) = (job_id IS NOT NULL)),
    CONSTRAINT ck_note_reissues_done CHECK (
        (status = 'done') = (finished_at IS NOT NULL)
        AND (status = 'done') = (sent_count IS NOT NULL)
        AND (sent_count IS NULL OR sent_count >= 0))
)
"""
REISSUE_INDEXES = [
    "CREATE INDEX ix_note_reissues_proposed ON note_reissues (proposed_at)",
]

# Staff propose and decide, as the person signed in, and only for days that have come by the
# database's own clock. The worker only finishes an approved request. Nothing else changes after
# a decision, and nothing is deleted or truncated, by anyone.
REISSUES_GUARD = f"""
CREATE FUNCTION public.finvest_note_reissues_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        today date := (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a note reissue is never deleted' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.status = 'approved' AND NEW.status = 'done' THEN
            IF public.finvest_actor_kind() IS DISTINCT FROM 'system'
                OR current_user <> '{WORKER_ROLE}'
                OR (to_jsonb(NEW) - '{{status,sent_count,finished_at}}'::text[])
                    <> (to_jsonb(OLD) - '{{status,sent_count,finished_at}}'::text[])
            THEN
                RAISE EXCEPTION 'only the worker finishes a note reissue' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff ask for notes to be sent again' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id()
                OR NEW.job_id IS NOT NULL OR NEW.issued_to > today
            THEN
                RAISE EXCEPTION 'a note reissue starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided note reissue never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status NOT IN ('approved', 'declined', 'withdrawn')
            OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{{status,decided_by,decided_at,decision_note,job_id}}'::text[])
                <> (to_jsonb(OLD) - '{{status,decided_by,decided_at,decision_note,job_id}}'::text[])
        THEN
            RAISE EXCEPTION 'a note reissue is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

REISSUES_RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_note_reissues_guard() FROM PUBLIC",
    "CREATE TRIGGER note_reissues_guard BEFORE INSERT OR UPDATE OR DELETE ON note_reissues "
    "FOR EACH ROW EXECUTE FUNCTION public.finvest_note_reissues_guard()",
    "CREATE TRIGGER note_reissues_no_truncate BEFORE TRUNCATE ON note_reissues "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_note_reissues_guard()",
    "REVOKE ALL ON note_reissues FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, job_id) "
    f"ON note_reissues TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (status, sent_count, finished_at) ON note_reissues TO {WORKER_ROLE}",
    # Staff only on the API's role, so a client transaction reads no request.
    "ALTER TABLE note_reissues ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY note_reissues_app ON note_reissues TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY note_reissues_worker ON note_reissues TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]

COPIES = """
CREATE TABLE document_email_copies (
    id push_id NOT NULL,
    reissue_id push_id NOT NULL,
    document_id push_id NOT NULL,
    client_id push_id NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'queued',
    failure_code varchar(64),
    provider varchar(32),
    provider_message_id varchar(200),
    queued_at timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT pk_document_email_copies PRIMARY KEY (id),
    -- One copy of each note for each request, so running the request twice sends nothing more.
    CONSTRAINT uq_document_email_copies UNIQUE (reissue_id, document_id),
    CONSTRAINT fk_document_email_copies_reissue
        FOREIGN KEY (reissue_id) REFERENCES note_reissues (id),
    CONSTRAINT fk_document_email_copies_document
        FOREIGN KEY (document_id) REFERENCES documents (id),
    CONSTRAINT ck_document_email_copies_status
        CHECK (status IN ('queued', 'sent', 'skipped', 'failed')),
    CONSTRAINT ck_document_email_copies_finished CHECK (
        (status = 'queued') = (finished_at IS NULL)
        AND (status IN ('skipped', 'failed')) = (failure_code IS NOT NULL)
        AND (status = 'sent') = (provider IS NOT NULL)),
    CONSTRAINT ck_document_email_copies_failure_code
        CHECK (failure_code ~ '^[a-z][a-z0-9_]{0,63}$')
);
CREATE INDEX ix_document_email_copies_document ON document_email_copies (document_id)
"""

COPIES_GUARD = f"""
CREATE FUNCTION public.finvest_document_email_copies_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'document email copies are never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system'
            OR current_user <> '{WORKER_ROLE}'
        THEN
            RAISE EXCEPTION 'only the worker sends a document again' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'queued' OR NEW.finished_at IS NOT NULL THEN
                RAISE EXCEPTION 'a document email copy is queued before it is sent'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (
                SELECT FROM public.note_reissues r
                JOIN public.documents d ON d.id = NEW.document_id
                WHERE r.id = NEW.reissue_id AND r.status = 'approved'
                    AND d.client_id = NEW.client_id AND d.kind = 'investment_note'
                    AND d.status = 'ready'
                    AND (r.client_id IS NULL OR r.client_id = NEW.client_id)
                    AND (d.finished_at AT TIME ZONE 'Africa/Kampala')::date
                        BETWEEN r.issued_from AND r.issued_to
            ) THEN
                RAISE EXCEPTION 'only a ready note inside an approved reissue is sent again'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'queued' THEN
            RAISE EXCEPTION 'a finished document email copy never changes'
                USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - '{{status,failure_code,provider,provider_message_id,finished_at}}'::text[])
            <> (to_jsonb(OLD) - '{{status,failure_code,provider,provider_message_id,finished_at}}'::text[])
        THEN
            RAISE EXCEPTION 'what a document email copy is for never changes'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

COPIES_RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_document_email_copies_guard() FROM PUBLIC",
    "CREATE TRIGGER document_email_copies_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON document_email_copies FOR EACH ROW "
    "EXECUTE FUNCTION public.finvest_document_email_copies_guard()",
    "CREATE TRIGGER document_email_copies_no_truncate BEFORE TRUNCATE ON document_email_copies "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_document_email_copies_guard()",
    "REVOKE ALL ON document_email_copies FROM PUBLIC",
    # The API only reads how a request's copies went.
    f"GRANT SELECT ON document_email_copies TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, failure_code, provider, provider_message_id, "
    f"finished_at) ON document_email_copies TO {WORKER_ROLE}",
    "ALTER TABLE document_email_copies ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY document_email_copies_app_read ON document_email_copies FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY document_email_copies_worker ON document_email_copies TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [
        REISSUES,
        *REISSUE_INDEXES,
        REISSUES_GUARD,
        *REISSUES_RULES,
        *split(COPIES),
        COPIES_GUARD,
        *COPIES_RULES,
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM note_reissues) OR EXISTS (SELECT FROM document_email_copies) THEN
        RAISE EXCEPTION 'refusing to drop the record of notes staff sent again';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A record of what was sent to a client is kept. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE document_email_copies")
    op.execute("DROP FUNCTION public.finvest_document_email_copies_guard()")
    op.execute("DROP TABLE note_reissues")
    op.execute("DROP FUNCTION public.finvest_note_reissues_guard()")
