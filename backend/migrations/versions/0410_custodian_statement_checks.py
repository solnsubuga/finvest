"""Add custodian statement checks: the custodian's holdings compared with the dealing desk's.

Revision ID: 0410_custodian_statement_checks
Revises: 0335_note_reissues

Staff upload the custodian's statement in any layout (PDF, spreadsheet, CSV or a photo) with the
day it states holdings at. The file is kept in private storage and named here by its key and
digest. The worker hands it to a reader (the Claude API) that copies each holding as printed,
and saves those rows. Finvest's own code parses the amounts and compares them with the bills
the dealing desk held that day, by currency and maturity date, and saves the result as items.

One member of staff reviews the check: corrects a row the reader got wrong, explains every
difference, and sends it for sign-off. A different one signs it off, or sends it back. A
signed-off check never changes. Nothing here moves money.

- `reconciliation_custodian_checks`: one row per uploaded statement.
- `reconciliation_custodian_rows`: what the reader copied, never changed, beside any correction.
- `reconciliation_custodian_items`: the comparison, worked out again after every correction.
- `reconciliation_custodian_notes`: the reviewer's explanation of each difference.

See docs/reconciliation.md#custodian.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0410_custodian_statement_checks"
down_revision: str | None = "0335_note_reissues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE reconciliation_custodian_checks (
    id push_id NOT NULL,
    statement_date date NOT NULL,
    file_key varchar(200) COLLATE "C" NOT NULL,
    file_sha256 char(64) COLLATE "C" NOT NULL,
    file_content_type varchar(80) COLLATE "C" NOT NULL,
    file_bytes integer NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'reading',
    uploaded_by push_id NOT NULL,
    uploaded_at timestamptz NOT NULL,
    -- What the reader said about the statement as a whole.
    reader varchar(60),
    read_at timestamptz,
    read_statement_date date,
    custodian_name varchar(120),
    account_reference varchar(60),
    amount_scale varchar(10),
    failure_code varchar(40) COLLATE "C",
    submitted_by push_id,
    submitted_at timestamptz,
    submit_note varchar(1000),
    signed_off_by push_id,
    signed_off_at timestamptz,
    sign_off_note varchar(1000),
    -- The last time a signer sent it back, and why.
    sent_back_by push_id,
    sent_back_at timestamptz,
    sent_back_note varchar(1000),
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_reconciliation_custodian_checks PRIMARY KEY (id),
    CONSTRAINT uq_reconciliation_custodian_checks_file UNIQUE (file_key),
    CONSTRAINT ck_reconciliation_custodian_checks_status CHECK (status IN (
        'reading', 'unreadable', 'to_review', 'waiting_sign_off', 'signed_off')),
    CONSTRAINT ck_reconciliation_custodian_checks_type CHECK (file_content_type IN (
        'application/pdf', 'text/csv', 'image/png', 'image/jpeg',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
    CONSTRAINT ck_reconciliation_custodian_checks_bytes CHECK (file_bytes > 0),
    CONSTRAINT ck_reconciliation_custodian_checks_scale
        CHECK (amount_scale IS NULL OR amount_scale IN ('one', 'thousand', 'million')),
    CONSTRAINT ck_reconciliation_custodian_checks_read CHECK (
        (status IN ('reading', 'unreadable')) = (amount_scale IS NULL)
        AND (status = 'unreadable') = (failure_code IS NOT NULL)
        AND (status = 'reading') = (read_at IS NULL)),
    CONSTRAINT ck_reconciliation_custodian_checks_submitted CHECK (
        (status IN ('waiting_sign_off', 'signed_off')) = (submitted_by IS NOT NULL)
        AND (submitted_by IS NULL) = (submitted_at IS NULL)),
    CONSTRAINT ck_reconciliation_custodian_checks_signed CHECK (
        (status = 'signed_off') = (signed_off_by IS NOT NULL)
        AND (signed_off_by IS NULL) = (signed_off_at IS NULL)),
    -- Two people: whoever sent it for sign-off never signs it off.
    CONSTRAINT ck_reconciliation_custodian_checks_two_people
        CHECK (signed_off_by IS NULL OR signed_off_by <> submitted_by)
);
CREATE INDEX ix_reconciliation_custodian_checks_uploaded
    ON reconciliation_custodian_checks (uploaded_at DESC, id);
CREATE TABLE reconciliation_custodian_rows (
    id push_id NOT NULL,
    check_id push_id NOT NULL,
    line_no integer NOT NULL,
    instrument varchar(300) NOT NULL,
    code varchar(40),
    face_value_text varchar(60) NOT NULL,
    currency_text varchar(12),
    matures_on_text varchar(20),
    location varchar(60) NOT NULL,
    source_text varchar(300) NOT NULL,
    -- What Finvest's code made of the copied text, or why it could not.
    currency varchar(3),
    face_minor bigint,
    matures_on date,
    problem varchar(40) COLLATE "C",
    -- A correction by the reviewer, who says why. The copied text above never changes.
    corrected_face_minor bigint,
    corrected_matures_on date,
    left_out boolean NOT NULL DEFAULT false,
    corrected_by push_id,
    corrected_at timestamptz,
    correction_reason varchar(500),
    CONSTRAINT pk_reconciliation_custodian_rows PRIMARY KEY (id),
    CONSTRAINT fk_reconciliation_custodian_rows_check
        FOREIGN KEY (check_id) REFERENCES reconciliation_custodian_checks (id),
    CONSTRAINT uq_reconciliation_custodian_rows_line UNIQUE (check_id, line_no),
    CONSTRAINT ck_reconciliation_custodian_rows_currency
        CHECK (currency IS NULL OR currency IN ('UGX', 'USD')),
    CONSTRAINT ck_reconciliation_custodian_rows_parsed CHECK (
        (problem IS NULL) = (face_minor IS NOT NULL AND matures_on IS NOT NULL)),
    CONSTRAINT ck_reconciliation_custodian_rows_positive CHECK (
        COALESCE(face_minor, 1) > 0 AND COALESCE(corrected_face_minor, 1) > 0),
    CONSTRAINT ck_reconciliation_custodian_rows_corrected CHECK (
        (corrected_by IS NULL) = (corrected_at IS NULL)
        AND (corrected_by IS NULL) = (correction_reason IS NULL)
        AND (corrected_by IS NOT NULL OR (corrected_face_minor IS NULL
            AND corrected_matures_on IS NULL AND NOT left_out)))
);
CREATE TABLE reconciliation_custodian_items (
    check_id push_id NOT NULL,
    item_key varchar(80) COLLATE "C" NOT NULL,
    position integer NOT NULL,
    kind varchar(30) NOT NULL,
    currency varchar(3),
    matures_on date,
    finvest_face_minor bigint NOT NULL DEFAULT 0,
    custodian_face_minor bigint NOT NULL DEFAULT 0,
    product_codes text[] NOT NULL DEFAULT '{}',
    row_ids text[] NOT NULL DEFAULT '{}',
    problem varchar(40) COLLATE "C",
    CONSTRAINT pk_reconciliation_custodian_items PRIMARY KEY (check_id, item_key),
    CONSTRAINT fk_reconciliation_custodian_items_check
        FOREIGN KEY (check_id) REFERENCES reconciliation_custodian_checks (id),
    CONSTRAINT ck_reconciliation_custodian_items_kind CHECK (kind IN (
        'match', 'amount_differs', 'missing_at_custodian', 'not_in_finvest', 'row_unreadable',
        'statement_date_differs', 'statement_date_unread')),
    CONSTRAINT ck_reconciliation_custodian_items_amounts
        CHECK (finvest_face_minor >= 0 AND custodian_face_minor >= 0)
);
CREATE TABLE reconciliation_custodian_notes (
    check_id push_id NOT NULL,
    item_key varchar(80) COLLATE "C" NOT NULL,
    note varchar(1000) NOT NULL,
    noted_by push_id NOT NULL,
    noted_at timestamptz NOT NULL,
    CONSTRAINT pk_reconciliation_custodian_notes PRIMARY KEY (check_id, item_key),
    CONSTRAINT fk_reconciliation_custodian_notes_check
        FOREIGN KEY (check_id) REFERENCES reconciliation_custodian_checks (id),
    CONSTRAINT ck_reconciliation_custodian_notes_note CHECK (btrim(note) <> '')
)
"""

# Who may change a check, and how far. The worker records what the reader said and nothing
# else; staff review and sign off; a signed-off check never changes again.
CHECK_GUARD = f"""
CREATE FUNCTION finvest_custodian_checks_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF actor IS DISTINCT FROM 'staff' OR NEW.uploaded_by <> finvest_actor_id()
                OR NEW.status <> 'reading' THEN
                RAISE EXCEPTION 'a custodian check starts as an upload by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('signed_off', 'unreadable') THEN
            RAISE EXCEPTION 'a finished custodian check never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.statement_date <> OLD.statement_date
            OR NEW.file_key <> OLD.file_key OR NEW.file_sha256 <> OLD.file_sha256
            OR NEW.file_content_type <> OLD.file_content_type
            OR NEW.file_bytes <> OLD.file_bytes OR NEW.uploaded_by <> OLD.uploaded_by
            OR NEW.uploaded_at <> OLD.uploaded_at THEN
            RAISE EXCEPTION 'a custodian check keeps the file it was uploaded with'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'reading' THEN
            IF NOT (actor = 'system' AND current_user = '{WORKER_ROLE}')
                OR NEW.status NOT IN ('to_review', 'unreadable') THEN
                RAISE EXCEPTION 'only the worker records what the reader said'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF actor IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff review a custodian check' USING ERRCODE = '42501';
        END IF;
        IF NEW.reader IS DISTINCT FROM OLD.reader OR NEW.read_at IS DISTINCT FROM OLD.read_at
            OR NEW.read_statement_date IS DISTINCT FROM OLD.read_statement_date
            OR NEW.custodian_name IS DISTINCT FROM OLD.custodian_name
            OR NEW.account_reference IS DISTINCT FROM OLD.account_reference
            OR NEW.amount_scale IS DISTINCT FROM OLD.amount_scale THEN
            RAISE EXCEPTION 'what the reader said never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'to_review' AND NEW.status = 'waiting_sign_off' THEN
            IF NEW.submitted_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a check is sent for sign-off by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'waiting_sign_off' AND NEW.status = 'signed_off' THEN
            IF NEW.signed_off_by <> finvest_actor_id() OR NEW.submitted_by <> OLD.submitted_by THEN
                RAISE EXCEPTION 'a check is signed off by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF OLD.status = 'waiting_sign_off' AND NEW.status = 'to_review' THEN
            IF NEW.sent_back_by <> finvest_actor_id() OR OLD.submitted_by = finvest_actor_id() THEN
                RAISE EXCEPTION 'a check is sent back by someone other than who sent it'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NOT (OLD.status = 'to_review' AND NEW.status = 'to_review') THEN
            RAISE EXCEPTION 'a custodian check does not move that way' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# Rows are written by the worker once. Staff may only correct them while the check is in review.
ROW_GUARD = f"""
CREATE FUNCTION finvest_custodian_rows_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        state text;
    BEGIN
        SELECT c.status INTO state FROM reconciliation_custodian_checks c
            WHERE c.id = NEW.check_id;
        IF TG_OP = 'INSERT' THEN
            IF NOT (actor = 'system' AND current_user = '{WORKER_ROLE}') OR state <> 'reading' THEN
                RAISE EXCEPTION 'only the worker records the rows it read'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF actor IS DISTINCT FROM 'staff' OR state <> 'to_review'
            OR NEW.corrected_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['corrected_face_minor', 'corrected_matures_on', 'left_out',
                                     'corrected_by', 'corrected_at', 'correction_reason'])
               <> (to_jsonb(OLD) - ARRAY['corrected_face_minor', 'corrected_matures_on',
                                        'left_out', 'corrected_by', 'corrected_at',
                                        'correction_reason']) THEN
            RAISE EXCEPTION 'a row is corrected by the reviewer while the check is in review'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

# The comparison and the notes change only while the check is being read or reviewed.
OPEN_GUARD = """
CREATE FUNCTION finvest_custodian_open_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        state text;
    BEGIN
        SELECT c.status INTO state FROM reconciliation_custodian_checks c
            WHERE c.id = COALESCE(NEW.check_id, OLD.check_id);
        IF state NOT IN ('reading', 'to_review') THEN
            RAISE EXCEPTION 'a custodian check is not open to change' USING ERRCODE = '42501';
        END IF;
        RETURN COALESCE(NEW, OLD);
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_custodian_checks_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_custodian_rows_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_custodian_open_guard() FROM PUBLIC",
    "CREATE TRIGGER custodian_checks_guard BEFORE INSERT OR UPDATE "
    "ON reconciliation_custodian_checks "
    "FOR EACH ROW EXECUTE FUNCTION finvest_custodian_checks_guard()",
    "CREATE TRIGGER custodian_rows_guard BEFORE INSERT OR UPDATE "
    "ON reconciliation_custodian_rows "
    "FOR EACH ROW EXECUTE FUNCTION finvest_custodian_rows_guard()",
    "CREATE TRIGGER custodian_items_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON reconciliation_custodian_items "
    "FOR EACH ROW EXECUTE FUNCTION finvest_custodian_open_guard()",
    "CREATE TRIGGER custodian_notes_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON reconciliation_custodian_notes "
    "FOR EACH ROW EXECUTE FUNCTION finvest_custodian_open_guard()",
    "REVOKE ALL ON reconciliation_custodian_checks FROM PUBLIC",
    "REVOKE ALL ON reconciliation_custodian_rows FROM PUBLIC",
    "REVOKE ALL ON reconciliation_custodian_items FROM PUBLIC",
    "REVOKE ALL ON reconciliation_custodian_notes FROM PUBLIC",
    # No DELETE of a check or a row for anyone: what was uploaded and read is the record.
    "GRANT SELECT, INSERT, UPDATE (status, submitted_by, submitted_at, submit_note, "
    "signed_off_by, signed_off_at, sign_off_note, sent_back_by, sent_back_at, sent_back_note, "
    f"updated_at) ON reconciliation_custodian_checks TO {APP_ROLE}",
    "GRANT SELECT, UPDATE (status, reader, read_at, read_statement_date, custodian_name, "
    "account_reference, amount_scale, failure_code, updated_at) "
    f"ON reconciliation_custodian_checks TO {WORKER_ROLE}",
    "GRANT SELECT, UPDATE (corrected_face_minor, corrected_matures_on, left_out, corrected_by, "
    f"corrected_at, correction_reason) ON reconciliation_custodian_rows TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON reconciliation_custodian_rows TO {WORKER_ROLE}",
    # The comparison is worked out again from the rows, so it is replaced whole.
    f"GRANT SELECT, INSERT, DELETE ON reconciliation_custodian_items TO {APP_ROLE}, {WORKER_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON reconciliation_custodian_notes TO {APP_ROLE}",
    f"GRANT SELECT ON reconciliation_custodian_notes TO {WORKER_ROLE}",
]
for _table in (
    "reconciliation_custodian_checks",
    "reconciliation_custodian_rows",
    "reconciliation_custodian_items",
    "reconciliation_custodian_notes",
):
    RULES += [
        f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY {_table}_app ON {_table} TO {APP_ROLE} "
        "USING (finvest_actor_kind() = 'staff') WITH CHECK (finvest_actor_kind() = 'staff')",
        f"CREATE POLICY {_table}_worker ON {_table} TO {WORKER_ROLE} "
        "USING (true) WITH CHECK (true)",
    ]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), CHECK_GUARD, ROW_GUARD, OPEN_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM reconciliation_custodian_checks) THEN
        RAISE EXCEPTION 'refusing to drop custodian statement checks';
    END IF;
END
$$
"""


def downgrade() -> None:
    # What was uploaded, read and signed off is part of the record. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    for table in (
        "reconciliation_custodian_notes",
        "reconciliation_custodian_items",
        "reconciliation_custodian_rows",
        "reconciliation_custodian_checks",
    ):
        op.execute(f"DROP TABLE {table}")
    for function in (
        "finvest_custodian_open_guard",
        "finvest_custodian_rows_guard",
        "finvest_custodian_checks_guard",
    ):
        op.execute(f"DROP FUNCTION {function}()")
