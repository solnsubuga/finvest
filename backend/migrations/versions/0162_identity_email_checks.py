"""Let a client prove their email address with a one-time link.

Revision ID: 0162_identity_email_checks
Revises: 0043_reconciliation_recorded

Statements and receipts go only to an address the client has proved is theirs. A link holds a
random token; only its SHA-256 is stored. The address counts as proved only while it is the
same address the link was sent to. The API cannot set `email_verified_at` itself: it calls
`identity_verify_email`, which spends a live link in the same statement.
"""

from alembic import op

revision = "0162_identity_email_checks"
down_revision = "0043_reconciliation_recorded"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE identity_clients ADD COLUMN email_verified_at timestamptz",
    "ALTER TABLE identity_clients ADD CONSTRAINT ck_identity_clients_email_verified "
    "CHECK (email_verified_at IS NULL OR email IS NOT NULL)",
    """
CREATE TABLE identity_email_checks (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    email text NOT NULL,
    token_hash bytea NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT pk_identity_email_checks PRIMARY KEY (id),
    CONSTRAINT fk_identity_email_checks_client
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT uq_identity_email_checks_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_identity_email_checks_token_hash CHECK (octet_length(token_hash) = 32),
    CONSTRAINT ck_identity_email_checks_expiry CHECK (expires_at > created_at)
)
""",
    "CREATE INDEX ix_identity_email_checks_client ON identity_email_checks (client_id) "
    "WHERE consumed_at IS NULL",
    "REVOKE ALL ON identity_email_checks FROM PUBLIC",
    "GRANT SELECT, INSERT ON identity_email_checks TO finvest_app",
    # A newer link replaces an older one, so the API may close unspent links.
    "GRANT UPDATE (consumed_at) ON identity_email_checks TO finvest_app",
    """
CREATE FUNCTION identity_verify_email(p_token_hash bytea, p_now timestamptz) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        check_row public.identity_email_checks%ROWTYPE;
    BEGIN
        UPDATE public.identity_email_checks SET consumed_at = p_now
        WHERE token_hash = p_token_hash AND consumed_at IS NULL AND expires_at > p_now
        RETURNING * INTO check_row;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        -- Proved only if the account still has the address the link was sent to.
        UPDATE public.identity_clients SET email_verified_at = p_now
        WHERE id = check_row.client_id AND email = check_row.email AND status = 'active';
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN check_row.client_id;
    END;
    $$
""",
    "REVOKE ALL ON FUNCTION identity_verify_email(bytea, timestamptz) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION identity_verify_email(bytea, timestamptz) TO finvest_app",
    # The worker decides whether a statement may go to the address.
    "GRANT SELECT (email_verified_at) ON identity_clients TO finvest_worker",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_email_checks)
        OR EXISTS (SELECT FROM identity_clients WHERE email_verified_at IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop proved email addresses';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("REVOKE SELECT (email_verified_at) ON identity_clients FROM finvest_worker")
    op.execute("DROP FUNCTION identity_verify_email(bytea, timestamptz)")
    op.execute("DROP TABLE identity_email_checks")
    op.execute("ALTER TABLE identity_clients DROP CONSTRAINT ck_identity_clients_email_verified")
    op.execute("ALTER TABLE identity_clients DROP COLUMN email_verified_at")
