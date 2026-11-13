"""Keep each migrated client's claim and the one-time links that start it.

Revision ID: 0072_migration_claims
Revises: 0082_dealing_maturities

A claim follows one client moved from Finvest 1.0 from the switch until they confirm their
investments. The steps and who may take each are in `app/modules/migration/domain/claim.py`,
and PostgreSQL holds the same rules:

* A claim moves only along the steps the rules allow, and only by the kind of actor the rules
  name for that step. A step is never skipped and never undone, except that staff close a
  reported problem and the identity checks module sends a failed selfie back.
* A link stores only the hash of its token. It is used once or cancelled once, never both,
  and a client has at most one live link.
* Nothing is deleted. Claim rows hold identifiers and a status, never a name, phone number or
  document: those stay with the identity module.

A visitor opening a link is not signed in, so the API looks a link up through one function that
returns only what that link opens, instead of reading the table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0072_migration_claims"
down_revision: str | None = "0082_dealing_maturities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

STATUSES = (
    "not_invited",
    "invited",
    "signed_in",
    "identity_submitted",
    "identity_confirmed",
    "confirmed",
    "problem_reported",
)
# (actor kind, from, to), the same table as `_TRANSITIONS` in domain/claim.py.
TRANSITIONS = (
    ("staff", "not_invited", "invited"),
    ("staff", "problem_reported", "identity_confirmed"),
    ("client", "invited", "signed_in"),
    ("client", "signed_in", "identity_submitted"),
    ("client", "identity_confirmed", "confirmed"),
    ("client", "identity_confirmed", "problem_reported"),
    ("system", "not_invited", "invited"),
    ("system", "identity_submitted", "identity_confirmed"),
    ("system", "identity_submitted", "signed_in"),
)
_STATUS_LIST = ", ".join(f"'{status}'" for status in STATUSES)
_ALLOWED = " OR ".join(
    f"(actor = '{kind}' AND OLD.status = '{before}' AND NEW.status = '{after}')"
    for kind, before, after in TRANSITIONS
)

TABLES = f"""
CREATE TABLE migration_claims (
    client_id push_id NOT NULL,
    legacy_client_ref varchar(64) NOT NULL,
    needs_id_check boolean NOT NULL,
    status varchar(24) NOT NULL,
    reminders_sent integer NOT NULL DEFAULT 0,
    problem_text varchar(1000),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_claims PRIMARY KEY (client_id),
    CONSTRAINT uq_migration_claims_legacy_client_ref UNIQUE (legacy_client_ref),
    CONSTRAINT ck_migration_claims_status CHECK (status IN ({_STATUS_LIST})),
    CONSTRAINT ck_migration_claims_reminders CHECK (reminders_sent BETWEEN 0 AND 8),
    CONSTRAINT ck_migration_claims_problem
        CHECK ((status = 'problem_reported') = (problem_text IS NOT NULL))
);
CREATE INDEX ix_migration_claims_status ON migration_claims (status);

CREATE TABLE migration_claim_links (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    token_hash char(64) NOT NULL,
    sent_at timestamptz NOT NULL,
    used_at timestamptz,
    cancelled_at timestamptz,
    CONSTRAINT pk_migration_claim_links PRIMARY KEY (id),
    CONSTRAINT fk_migration_claim_links_client_id
        FOREIGN KEY (client_id) REFERENCES migration_claims (client_id),
    CONSTRAINT uq_migration_claim_links_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_migration_claim_links_token_hash CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT ck_migration_claim_links_one_end CHECK (used_at IS NULL OR cancelled_at IS NULL)
);
-- At most one live link per client: a fresh link cancels the one before it.
CREATE UNIQUE INDEX uq_migration_claim_links_live ON migration_claim_links (client_id)
    WHERE used_at IS NULL AND cancelled_at IS NULL
"""

FUNCTIONS = [
    f"""
CREATE FUNCTION finvest_migration_claim_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'not_invited' OR NEW.reminders_sent <> 0
                OR NEW.problem_text IS NOT NULL THEN
                RAISE EXCEPTION 'a claim starts not invited, with no reminders or problem'
                    USING ERRCODE = '23514', CONSTRAINT = 'migration_claim_step';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'migration claims are never removed' USING ERRCODE = '42501';
        END IF;
        IF NEW.client_id <> OLD.client_id OR NEW.legacy_client_ref <> OLD.legacy_client_ref
            OR NEW.needs_id_check <> OLD.needs_id_check OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a claim keeps who it is for' USING ERRCODE = '42501';
        END IF;
        IF NEW.reminders_sent < OLD.reminders_sent THEN
            RAISE EXCEPTION 'reminders are only counted up' USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> OLD.status AND NOT ({_ALLOWED}) THEN
            RAISE EXCEPTION 'a claim cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = '23514', CONSTRAINT = 'migration_claim_step';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    """
CREATE FUNCTION finvest_migration_claim_link_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'claim links are never removed' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id
            OR NEW.token_hash <> OLD.token_hash OR NEW.sent_at <> OLD.sent_at
            OR (OLD.used_at IS NOT NULL AND NEW.used_at IS DISTINCT FROM OLD.used_at)
            OR (OLD.cancelled_at IS NOT NULL
                AND NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at) THEN
            RAISE EXCEPTION 'a claim link is used or cancelled once, and nothing else changes'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    # What a link opens, for a visitor who is not signed in. Runs as the table owner, so such a
    # visitor needs no read access to links, and returns nothing for an unknown hash.
    """
CREATE FUNCTION finvest_migration_claim_link(hash text)
    RETURNS TABLE (
        link_id public.push_id, client_id public.push_id, sent_at timestamptz,
        used_at timestamptz, cancelled_at timestamptz, status varchar
    )
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT l.id, l.client_id, l.sent_at, l.used_at, l.cancelled_at, c.status
        FROM public.migration_claim_links AS l
        JOIN public.migration_claims AS c ON c.client_id = l.client_id
        WHERE l.token_hash = hash
    $$
""",
]

STAFF = "finvest_actor_kind() = 'staff'"
OWN = "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())"
SYSTEM = "finvest_actor_kind() = 'system'"

RULES = [
    *(
        f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC"
        for name in (
            "finvest_migration_claim_guard()",
            "finvest_migration_claim_link_guard()",
            "finvest_migration_claim_link(text)",
        )
    ),
    f"GRANT EXECUTE ON FUNCTION finvest_migration_claim_link(text) TO {APP_ROLE}",
    "CREATE TRIGGER migration_claims_guard BEFORE INSERT OR UPDATE OR DELETE ON migration_claims "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_claim_guard()",
    "CREATE TRIGGER migration_claims_no_truncate BEFORE TRUNCATE ON migration_claims "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_migration_claim_guard()",
    "CREATE TRIGGER migration_claim_links_guard BEFORE UPDATE OR DELETE ON migration_claim_links "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_claim_link_guard()",
    "CREATE TRIGGER migration_claim_links_no_truncate BEFORE TRUNCATE ON migration_claim_links "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_migration_claim_link_guard()",
    *(
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
        for table in ("migration_claims", "migration_claim_links")
    ),
    *(
        f"REVOKE ALL ON {table} FROM PUBLIC"
        for table in ("migration_claims", "migration_claim_links")
    ),
    # The switch, in the worker, creates claims and sends links and reminders. The API moves a
    # claim for the client or staff member signed in, and staff send a fresh link.
    f"GRANT SELECT, INSERT, UPDATE ON migration_claims TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON migration_claim_links TO {WORKER_ROLE}",
    f"GRANT SELECT, UPDATE ON migration_claims TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON migration_claim_links TO {APP_ROLE}",
    f"CREATE POLICY migration_claims_worker ON migration_claims FOR ALL TO {WORKER_ROLE} "
    f"USING ({SYSTEM}) WITH CHECK ({SYSTEM})",
    f"CREATE POLICY migration_claim_links_worker ON migration_claim_links FOR ALL "
    f"TO {WORKER_ROLE} USING ({SYSTEM}) WITH CHECK ({SYSTEM})",
    f"CREATE POLICY migration_claims_app_read ON migration_claims FOR SELECT TO {APP_ROLE} "
    f"USING ({STAFF} OR {OWN})",
    f"CREATE POLICY migration_claims_app_update ON migration_claims FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF} OR {OWN}) WITH CHECK ({STAFF} OR {OWN})",
    # Staff send and cancel links. A client's own link is marked used when they claim.
    f"CREATE POLICY migration_claim_links_app_read ON migration_claim_links FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF} OR {OWN})",
    f"CREATE POLICY migration_claim_links_app_insert ON migration_claim_links FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY migration_claim_links_app_update ON migration_claim_links FOR UPDATE "
    f"TO {APP_ROLE} USING ({STAFF} OR {OWN}) WITH CHECK ({STAFF} OR {OWN})",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM migration_claims) THEN
        RAISE EXCEPTION 'refusing to drop migration claims';
    END IF;
END
$$
"""


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *FUNCTIONS, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the tables would forget who has claimed their account.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE migration_claim_links")
    op.execute("DROP TABLE migration_claims")
    for function in (
        "finvest_migration_claim_link(text)",
        "finvest_migration_claim_link_guard()",
        "finvest_migration_claim_guard()",
    ):
        op.execute(f"DROP FUNCTION {function}")
