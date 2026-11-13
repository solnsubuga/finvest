"""Create client and staff accounts, sessions, sign-in challenges and attempt limits."""

from alembic import op

revision = "0005_identity"
down_revision = "0013_ledger_corrections"
branch_labels = None
depends_on = None

# The identity module owns these tables. `identity_clients.id` is the client ID that every other
# module refers to. Profiles, documents and account numbers belong to the clients module.
TABLES = """
CREATE TABLE identity_clients (
    id push_id NOT NULL,
    phone text NOT NULL,
    email text,
    password_hash text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    restricted_until timestamptz,
    password_changed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_identity_clients PRIMARY KEY (id),
    CONSTRAINT uq_identity_clients_phone UNIQUE (phone),
    CONSTRAINT ck_identity_clients_phone CHECK (phone ~ '^\\+[1-9][0-9]{7,14}$'),
    CONSTRAINT ck_identity_clients_email
        CHECK (email = lower(email) AND email ~ '^[^@\\s]+@[^@\\s]+$' AND length(email) <= 254),
    CONSTRAINT ck_identity_clients_status CHECK (status IN ('active', 'locked', 'closed'))
);

CREATE TABLE identity_staff (
    id push_id NOT NULL,
    email text NOT NULL,
    name text NOT NULL,
    role text NOT NULL,
    password_hash text,
    status text NOT NULL DEFAULT 'invited',
    password_changed_at timestamptz,
    created_at timestamptz NOT NULL,
    created_by push_id,
    CONSTRAINT pk_identity_staff PRIMARY KEY (id),
    CONSTRAINT uq_identity_staff_email UNIQUE (email),
    CONSTRAINT fk_identity_staff_created_by_identity_staff
        FOREIGN KEY (created_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_identity_staff_email
        CHECK (email = lower(email) AND email ~ '^[^@\\s]+@[^@\\s]+$' AND length(email) <= 254),
    CONSTRAINT ck_identity_staff_name CHECK (btrim(name) <> '' AND length(name) <= 120),
    CONSTRAINT ck_identity_staff_role
        CHECK (role IN ('admin', 'operations', 'finance', 'compliance', 'support')),
    CONSTRAINT ck_identity_staff_status CHECK (status IN ('invited', 'active', 'disabled')),
    CONSTRAINT ck_identity_staff_active_has_password
        CHECK (status <> 'active' OR password_hash IS NOT NULL)
);

-- The first step of sign-in, passed. It opens nothing by itself: a second step must follow.
CREATE TABLE identity_login_challenges (
    id push_id NOT NULL,
    actor_kind text NOT NULL,
    actor_id push_id NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT pk_identity_login_challenges PRIMARY KEY (id),
    CONSTRAINT ck_identity_login_challenges_actor_kind CHECK (actor_kind IN ('client', 'staff'))
);
CREATE INDEX ix_identity_login_challenges_expires_at ON identity_login_challenges (expires_at);

-- Only a digest of the session token is stored, so a copy of this table opens no session.
CREATE TABLE identity_sessions (
    id push_id NOT NULL,
    actor_kind text NOT NULL,
    actor_id push_id NOT NULL,
    token_hash bytea NOT NULL,
    second_step text NOT NULL,
    transport text NOT NULL,
    created_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revoked_reason text,
    CONSTRAINT pk_identity_sessions PRIMARY KEY (id),
    CONSTRAINT uq_identity_sessions_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_identity_sessions_actor_kind CHECK (actor_kind IN ('client', 'staff')),
    CONSTRAINT ck_identity_sessions_token_hash CHECK (octet_length(token_hash) = 32),
    CONSTRAINT ck_identity_sessions_second_step
        CHECK (second_step IN ('passkey', 'authenticator', 'code', 'signup')),
    -- Staff have no texted or emailed code and never sign up: a staff session always used a
    -- passkey or an authenticator app.
    CONSTRAINT ck_identity_sessions_staff_second_step
        CHECK (actor_kind <> 'staff' OR second_step IN ('passkey', 'authenticator')),
    CONSTRAINT ck_identity_sessions_transport CHECK (transport IN ('cookie', 'token')),
    CONSTRAINT ck_identity_sessions_expiry CHECK (idle_expires_at <= absolute_expires_at),
    CONSTRAINT ck_identity_sessions_revoked CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
);
CREATE INDEX ix_identity_sessions_actor ON identity_sessions (actor_kind, actor_id)
    WHERE revoked_at IS NULL;

-- Attempt counters keyed by a digest, so no phone number, email or address is stored here.
CREATE TABLE identity_throttles (
    key_hash bytea NOT NULL,
    attempts integer NOT NULL,
    window_started_at timestamptz NOT NULL,
    CONSTRAINT pk_identity_throttles PRIMARY KEY (key_hash),
    CONSTRAINT ck_identity_throttles_key_hash CHECK (octet_length(key_hash) = 32)
)
"""

# Sign-in runs before anyone is known, so these tables cannot be fenced by the actor. They are
# fenced by role instead: only the API reads or writes them, and it can delete nothing but a
# spent attempt counter. The worker has no access at all.
GRANTS = [
    "REVOKE ALL ON identity_clients, identity_staff, identity_login_challenges, "
    "identity_sessions, identity_throttles FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE ON identity_clients, identity_staff, "
    "identity_login_challenges, identity_sessions TO finvest_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON identity_throttles TO finvest_app",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    for statement in GRANTS:
        op.execute(statement)


# Accounts and sessions are records of who may sign in. A downgrade must not erase them.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_clients) OR EXISTS (SELECT FROM identity_staff)
        OR EXISTS (SELECT FROM identity_sessions) THEN
        RAISE EXCEPTION 'refusing to drop sign-in tables that hold accounts';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(
        "DROP TABLE identity_throttles, identity_sessions, identity_login_challenges, "
        "identity_staff, identity_clients"
    )
