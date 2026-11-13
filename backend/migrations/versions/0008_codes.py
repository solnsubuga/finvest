"""Create one-time codes for signup, the sign-in fallback and password reset.

Revision ID: 0008_codes
Revises: 0092_email_delivery
"""

from alembic import op

revision = "0008_codes"
down_revision = "0092_email_delivery"
branch_labels = None
depends_on = None

# A six-digit code sent by SMS or email. Only a digest is stored, it allows five tries, and it
# works once. For signup the row also carries the number being proved and, once the code is
# right, the digest of a ticket that lets the same person set a password.
TABLES = """
CREATE TABLE identity_codes (
    id push_id NOT NULL,
    purpose text NOT NULL,
    channel text NOT NULL,
    phone text,
    email text,
    actor_id push_id,
    bound_to push_id,
    code_hash bytea NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    ticket_hash bytea,
    ticket_expires_at timestamptz,
    ticket_consumed_at timestamptz,
    CONSTRAINT pk_identity_codes PRIMARY KEY (id),
    CONSTRAINT uq_identity_codes_ticket_hash UNIQUE (ticket_hash),
    CONSTRAINT ck_identity_codes_purpose CHECK (purpose IN ('signup', 'login', 'password_reset')),
    CONSTRAINT ck_identity_codes_channel CHECK (channel IN ('sms', 'email')),
    CONSTRAINT ck_identity_codes_code_hash CHECK (octet_length(code_hash) = 32),
    CONSTRAINT ck_identity_codes_attempts CHECK (attempts BETWEEN 0 AND 5),
    CONSTRAINT ck_identity_codes_signup CHECK (purpose <> 'signup' OR phone IS NOT NULL),
    CONSTRAINT ck_identity_codes_login
        CHECK (purpose <> 'login' OR (actor_id IS NOT NULL AND bound_to IS NOT NULL)),
    CONSTRAINT ck_identity_codes_ticket
        CHECK ((ticket_hash IS NULL) = (ticket_expires_at IS NULL))
);
CREATE INDEX ix_identity_codes_bound_to ON identity_codes (bound_to) WHERE consumed_at IS NULL;
CREATE INDEX ix_identity_codes_expires_at ON identity_codes (expires_at)
"""

GRANTS = [
    "REVOKE ALL ON identity_codes FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE ON identity_codes TO finvest_app",
]


# The API changes only these columns of an account. A role, a status change for a client, a
# phone number or an email address is never changed by a bug or an injected statement in a
# sign-in route. 0005 granted UPDATE on whole rows.
ACCOUNT_COLUMNS = [
    "REVOKE UPDATE ON identity_clients, identity_staff FROM finvest_app",
    "GRANT UPDATE (password_hash, password_changed_at, restricted_until) "
    "ON identity_clients TO finvest_app",
    "GRANT UPDATE (password_hash, password_changed_at, status) ON identity_staff TO finvest_app",
    # The worker sends messages to clients. It may read how to reach them and nothing else.
    "GRANT SELECT (id, phone, email, status) ON identity_clients TO finvest_worker",
]
WHOLE_ROWS = [
    "REVOKE UPDATE (password_hash, password_changed_at, restricted_until) "
    "ON identity_clients FROM finvest_app",
    "REVOKE UPDATE (password_hash, password_changed_at, status) ON identity_staff FROM finvest_app",
    "GRANT UPDATE ON identity_clients, identity_staff TO finvest_app",
    "REVOKE SELECT (id, phone, email, status) ON identity_clients FROM finvest_worker",
]

# A queued message can hold a one-time code. `deliver_by` is when it stops being worth sending,
# and the hourly purge clears the content of anything still queued after it.
DELIVER_BY = "ALTER TABLE notification_messages ADD COLUMN deliver_by timestamptz"

# A code row is also the record of a signup ticket and of a reset. Refuse to drop them.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_codes) THEN
        RAISE EXCEPTION 'refusing to drop sign-in codes that were issued';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    for statement in [*GRANTS, *ACCOUNT_COLUMNS, DELIVER_BY]:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in WHOLE_ROWS:
        op.execute(statement)
    op.execute("ALTER TABLE notification_messages DROP COLUMN deliver_by")
    op.execute("DROP TABLE identity_codes")
