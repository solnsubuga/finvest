"""Look up the name the network holds for a client's phone number when they hand in their ID.

Revision ID: 0370_kyc_phone_names
Revises: 0333_maturity_pay_interest

When a client hands in their identity check, the worker asks ioTec Verify for the name the
mobile network holds for the client's own phone number, the same lookup a mobile money payout
number gets. The name is compared with the name the client confirmed from their document, and
staff see the network's name and how it compares on the check. It never approves or rejects a
client: a person decides. See docs/identity-checks.md#phone-number-name.

`kyc_phone_names` holds each lookup. The client asks for one in the transaction that hands the
check in; only the worker records what ioTec said, and an answer is final. A check has at most
three, one at a time, because each costs a fee at ioTec.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0370_kyc_phone_names"
down_revision: str | None = "0333_maturity_pay_interest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = [
    """
CREATE TABLE kyc_phone_names (
    id push_id NOT NULL,
    check_id push_id NOT NULL,
    number smallint NOT NULL,
    status varchar(16) NOT NULL,
    request_id uuid,
    registered_name varchar(120),
    name_match varchar(16),
    error_code varchar(64) COLLATE "C",
    created_at timestamptz NOT NULL,
    answered_at timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_kyc_phone_names PRIMARY KEY (id),
    CONSTRAINT fk_kyc_phone_names_check FOREIGN KEY (check_id) REFERENCES kyc_checks (id),
    CONSTRAINT uq_kyc_phone_names_number UNIQUE (check_id, number),
    CONSTRAINT ck_kyc_phone_names_number CHECK (number BETWEEN 1 AND 3),
    CONSTRAINT ck_kyc_phone_names_status CHECK (
        status IN ('queued', 'sent', 'found', 'no_name', 'unanswered')),
    CONSTRAINT ck_kyc_phone_names_name CHECK (
        (status = 'found') = (registered_name IS NOT NULL)
        AND (registered_name IS NULL OR length(btrim(registered_name)) > 0)),
    CONSTRAINT ck_kyc_phone_names_match CHECK (
        (status = 'found') = (name_match IS NOT NULL)
        AND (name_match IS NULL OR name_match IN ('exact', 'partial', 'different'))),
    CONSTRAINT ck_kyc_phone_names_request CHECK (
        status NOT IN ('sent', 'found', 'no_name') OR request_id IS NOT NULL),
    CONSTRAINT ck_kyc_phone_names_answered CHECK (
        (status IN ('found', 'no_name', 'unanswered')) = (answered_at IS NOT NULL)),
    CONSTRAINT ck_kyc_phone_names_error_code CHECK (
        error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$')
)
""",
    "CREATE INDEX ix_kyc_phone_names_check ON kyc_phone_names (check_id, number DESC)",
]

GUARD = f"""
CREATE FUNCTION finvest_kyc_phone_names_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        step text;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'phone name lookups are never deleted' USING ERRCODE = '42501';
        END IF;
        IF NOT (finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}') THEN
            RAISE EXCEPTION 'only the worker records a phone name lookup' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.check_id, NEW.number, NEW.created_at)
               IS DISTINCT FROM (OLD.id, OLD.check_id, OLD.number, OLD.created_at) THEN
            RAISE EXCEPTION 'a phone name lookup is for one check, once' USING ERRCODE = '42501';
        END IF;
        IF OLD.request_id IS NOT NULL AND NEW.request_id IS DISTINCT FROM OLD.request_id THEN
            RAISE EXCEPTION 'a phone name lookup keeps its request' USING ERRCODE = '42501';
        END IF;
        step := OLD.status || '>' || NEW.status;
        IF step NOT IN ('queued>sent', 'queued>unanswered', 'sent>found', 'sent>no_name',
                        'sent>unanswered') THEN
            RAISE EXCEPTION 'an answered phone name lookup is final' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_phone_names_guard() FROM PUBLIC",
    "CREATE TRIGGER kyc_phone_names_guard BEFORE UPDATE OR DELETE ON kyc_phone_names "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_phone_names_guard()",
    "CREATE TRIGGER kyc_phone_names_no_truncate BEFORE TRUNCATE ON kyc_phone_names "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_kyc_phone_names_guard()",
    "ALTER TABLE kyc_phone_names ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON kyc_phone_names FROM PUBLIC",
    f"GRANT SELECT, INSERT ON kyc_phone_names TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE ON kyc_phone_names TO {WORKER_ROLE}",
    # The subquery runs under the kyc_checks policies: staff see every lookup, a client their own.
    f"CREATE POLICY kyc_phone_names_app_read ON kyc_phone_names FOR SELECT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "USING (check_id IN (SELECT id FROM kyc_checks))",
    # A client asks only for a lookup of their own check, with nothing answered yet.
    f"CREATE POLICY kyc_phone_names_app_insert ON kyc_phone_names FOR INSERT TO {APP_ROLE} "  # noqa: S608 - constant role name
    "WITH CHECK (finvest_actor_kind() = 'client' AND status = 'queued' "
    "AND request_id IS NULL AND registered_name IS NULL AND name_match IS NULL "
    "AND answered_at IS NULL AND error_code IS NULL "
    "AND check_id IN (SELECT id FROM kyc_checks))",
    f"CREATE POLICY kyc_phone_names_worker_read ON kyc_phone_names FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY kyc_phone_names_worker_update ON kyc_phone_names FOR UPDATE "
    f"TO {WORKER_ROLE} USING (finvest_actor_kind() = 'system')",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_phone_names) THEN
        RAISE EXCEPTION 'refusing to drop phone name lookups';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in [*TABLE, GUARD, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Identity records are kept for the regulator; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE kyc_phone_names")
    op.execute("DROP FUNCTION finvest_kyc_phone_names_guard()")
