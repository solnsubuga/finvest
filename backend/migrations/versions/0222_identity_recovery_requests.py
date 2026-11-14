"""Hold requests from people who cannot get into their account, for staff to follow up.

Revision ID: 0222_identity_recovery_requests
Revises: 0221_identity_app_pins

Someone who lost their phone or changed their number cannot finish a sign-in. They tell us the
number on their account, a number to call them on now and what happened, without signing in.
Staff call them back, check who they are, and change the number through the two-person phone
change (0160), then close the request.

The caller is not signed in, so `identity_request_recovery` inserts the row as the table owner.
It matches the account by its number and stores the number to call only when an account
matched; for a number with no account it stores a row that holds nothing personal, so the work
and the answer are the same either way. Only staff can read or close a request, under row
security. There is no free text.

An account has at most one open request. While one is open, a new ask for the same number
records nothing and the function returns no account, so the account's own number and email are
told once per open request rather than once per ask. The caller's answer is the same either way.
"""

from alembic import op

revision = "0222_identity_recovery_requests"
down_revision = "0221_identity_app_pins"
branch_labels = None
depends_on = None

TABLE = """
CREATE TABLE identity_recovery_requests (
    id push_id NOT NULL,
    client_id push_id,
    contact_phone text,
    reason text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    closed_at timestamptz,
    closed_by push_id,
    outcome text,
    CONSTRAINT pk_identity_recovery_requests PRIMARY KEY (id),
    CONSTRAINT fk_identity_recovery_requests_client_id_identity_clients
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT fk_identity_recovery_requests_closed_by_identity_staff
        FOREIGN KEY (closed_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_identity_recovery_requests_reason
        CHECK (reason IN ('lost_phone', 'new_number', 'no_codes', 'other')),
    CONSTRAINT ck_identity_recovery_requests_status
        CHECK (status IN ('open', 'closed', 'no_account')),
    CONSTRAINT ck_identity_recovery_requests_contact
        CHECK (contact_phone IS NULL OR contact_phone ~ '^\\+[1-9][0-9]{7,14}$'),
    -- A request for a number with no account holds nothing personal and is never worked.
    CONSTRAINT ck_identity_recovery_requests_no_account CHECK (
        (status = 'no_account') = (client_id IS NULL)
        AND (client_id IS NULL OR contact_phone IS NOT NULL)
        AND (client_id IS NOT NULL OR contact_phone IS NULL)
    ),
    CONSTRAINT ck_identity_recovery_requests_closed CHECK (
        (status = 'closed') = (closed_at IS NOT NULL)
        AND (closed_at IS NULL) = (closed_by IS NULL)
        AND (closed_at IS NULL) = (outcome IS NULL)
    ),
    CONSTRAINT ck_identity_recovery_requests_outcome
        CHECK (outcome IS NULL OR outcome IN ('number_changed', 'helped', 'not_the_client'))
)
"""

REQUEST = """
CREATE FUNCTION identity_request_recovery(
    p_id text, p_phone text, p_contact text, p_reason text, p_now timestamptz
) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        matched text;
        opened text;
    BEGIN
        SELECT id INTO matched FROM public.identity_clients
        WHERE phone = p_phone AND status IN ('active', 'locked');
        IF matched IS NULL THEN
            INSERT INTO public.identity_recovery_requests
                (id, client_id, contact_phone, reason, status, created_at)
            VALUES (p_id, NULL, NULL, p_reason, 'no_account', p_now);
            RETURN NULL;
        END IF;
        -- One open request per account. While one is open, a new ask records nothing and
        -- returns nothing, so the account is told once, not once per ask.
        INSERT INTO public.identity_recovery_requests
            (id, client_id, contact_phone, reason, status, created_at)
        VALUES (p_id, matched, p_contact, p_reason, 'open', p_now)
        ON CONFLICT (client_id) WHERE status = 'open' DO NOTHING
        RETURNING client_id INTO opened;
        RETURN opened;
    END;
    $$
"""

GUARD = """
CREATE FUNCTION identity_recovery_requests_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF OLD.status <> 'open' OR NEW.status <> 'closed'
            OR NEW.closed_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,closed_at,closed_by,outcome}'::text[])
                <> (to_jsonb(OLD) - '{status,closed_at,closed_by,outcome}'::text[]) THEN
            RAISE EXCEPTION 'a recovery request is only closed, once, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    TABLE,
    "CREATE INDEX ix_identity_recovery_requests_open ON identity_recovery_requests (created_at) "
    "WHERE status = 'open'",
    "CREATE UNIQUE INDEX uq_identity_recovery_requests_open_client "
    "ON identity_recovery_requests (client_id) WHERE status = 'open'",
    REQUEST,
    GUARD,
    "ALTER TABLE identity_recovery_requests ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY identity_recovery_requests_staff ON identity_recovery_requests "
    "TO finvest_app USING (public.finvest_actor_kind() = 'staff') "
    "WITH CHECK (public.finvest_actor_kind() = 'staff')",
    "REVOKE ALL ON identity_recovery_requests FROM PUBLIC",
    "GRANT SELECT, UPDATE ON identity_recovery_requests TO finvest_app",
    "CREATE TRIGGER tr_identity_recovery_requests_guard BEFORE UPDATE "
    "ON identity_recovery_requests FOR EACH ROW "
    "EXECUTE FUNCTION identity_recovery_requests_guard()",
    "REVOKE ALL ON FUNCTION identity_recovery_requests_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION identity_request_recovery(text, text, text, text, timestamptz) "
    "FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION identity_request_recovery(text, text, text, text, timestamptz) "
    "TO finvest_app",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_recovery_requests WHERE status = 'open') THEN
        RAISE EXCEPTION 'refusing to drop recovery requests staff have not closed';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE identity_recovery_requests")
    op.execute("DROP FUNCTION identity_recovery_requests_guard()")
    op.execute("DROP FUNCTION identity_request_recovery(text, text, text, text, timestamptz)")
