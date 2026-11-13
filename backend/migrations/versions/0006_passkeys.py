"""Create passkeys, authenticator apps, one-time challenges and staff enrolment links."""

from alembic import op

revision = "0006_passkeys"
down_revision = "0051_kyc_sealed_details"
branch_labels = None
depends_on = None

TABLES = """
-- WebAuthn: the device keeps the private key. Only the public key and counter are here.
CREATE TABLE identity_passkeys (
    id push_id NOT NULL,
    actor_kind text NOT NULL,
    actor_id push_id NOT NULL,
    credential_id bytea NOT NULL,
    public_key bytea NOT NULL,
    sign_count bigint NOT NULL,
    transports text[] NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    last_used_at timestamptz,
    removed_at timestamptz,
    CONSTRAINT pk_identity_passkeys PRIMARY KEY (id),
    CONSTRAINT uq_identity_passkeys_credential_id UNIQUE (credential_id),
    CONSTRAINT ck_identity_passkeys_actor_kind CHECK (actor_kind IN ('client', 'staff')),
    CONSTRAINT ck_identity_passkeys_credential_id
        CHECK (octet_length(credential_id) BETWEEN 1 AND 1023),
    CONSTRAINT ck_identity_passkeys_public_key CHECK (octet_length(public_key) BETWEEN 1 AND 2048),
    CONSTRAINT ck_identity_passkeys_sign_count CHECK (sign_count >= 0),
    CONSTRAINT ck_identity_passkeys_transports CHECK (cardinality(transports) <= 8),
    CONSTRAINT ck_identity_passkeys_name CHECK (btrim(name) <> '' AND length(name) <= 60)
);
CREATE INDEX ix_identity_passkeys_actor ON identity_passkeys (actor_kind, actor_id)
    WHERE removed_at IS NULL;

-- An authenticator app (TOTP) as a member of staff's second step. The shared secret has to be
-- readable to check a code, so it is stored encrypted with a key the database never sees.
-- `last_step` is the time step of the last accepted code: a code works once.
CREATE TABLE identity_authenticators (
    id push_id NOT NULL,
    actor_kind text NOT NULL,
    actor_id push_id NOT NULL,
    secret_sealed bytea NOT NULL,
    bound_to push_id NOT NULL,
    created_at timestamptz NOT NULL,
    confirmed_at timestamptz,
    last_step bigint,
    removed_at timestamptz,
    CONSTRAINT pk_identity_authenticators PRIMARY KEY (id),
    CONSTRAINT ck_identity_authenticators_actor_kind CHECK (actor_kind = 'staff'),
    CONSTRAINT ck_identity_authenticators_secret
        CHECK (octet_length(secret_sealed) BETWEEN 29 AND 256)
);
CREATE UNIQUE INDEX uq_identity_authenticators_actor
    ON identity_authenticators (actor_kind, actor_id)
    WHERE confirmed_at IS NOT NULL AND removed_at IS NULL;
CREATE INDEX ix_identity_authenticators_bound_to ON identity_authenticators (bound_to);

-- A challenge works once, for five minutes, for one purpose, one person and the one sign-in,
-- enrolment or session it was made for. Only its SHA-256 is stored.
CREATE TABLE identity_passkey_challenges (
    challenge_hash bytea NOT NULL,
    purpose text NOT NULL,
    actor_kind text NOT NULL,
    actor_id push_id NOT NULL,
    bound_to push_id NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT pk_identity_passkey_challenges PRIMARY KEY (challenge_hash),
    CONSTRAINT ck_identity_passkey_challenges_hash CHECK (octet_length(challenge_hash) = 32),
    CONSTRAINT ck_identity_passkey_challenges_purpose CHECK (purpose IN ('register', 'sign_in')),
    CONSTRAINT ck_identity_passkey_challenges_actor_kind CHECK (actor_kind IN ('client', 'staff'))
);
CREATE INDEX ix_identity_passkey_challenges_expires_at
    ON identity_passkey_challenges (expires_at);

-- The one-time link a new member of staff uses to set a password and add a passkey. Only a
-- digest of the link's token is stored.
CREATE TABLE identity_staff_enrolments (
    id push_id NOT NULL,
    staff_id push_id NOT NULL,
    token_hash bytea NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT pk_identity_staff_enrolments PRIMARY KEY (id),
    CONSTRAINT uq_identity_staff_enrolments_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_identity_staff_enrolments_staff_id_identity_staff
        FOREIGN KEY (staff_id) REFERENCES identity_staff (id),
    CONSTRAINT ck_identity_staff_enrolments_token_hash CHECK (octet_length(token_hash) = 32)
);
CREATE INDEX ix_identity_staff_enrolments_staff_id ON identity_staff_enrolments (staff_id)
    WHERE consumed_at IS NULL
"""

# Like the rest of sign-in, these are for the API alone. A passkey or authenticator app is never
# deleted, only marked removed. Spent challenges are short-lived and the API clears them.
GRANTS = [
    "REVOKE ALL ON identity_passkeys, identity_authenticators, identity_passkey_challenges, "
    "identity_staff_enrolments FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE ON identity_passkeys, identity_authenticators, "
    "identity_staff_enrolments TO finvest_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON identity_passkey_challenges TO finvest_app",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    for statement in GRANTS:
        op.execute(statement)


# Dropping these would leave every account without its second step.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_passkeys) OR EXISTS (SELECT FROM identity_authenticators) THEN
        RAISE EXCEPTION 'refusing to drop second steps that are in use';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(
        "DROP TABLE identity_staff_enrolments, identity_passkey_challenges, "
        "identity_authenticators, identity_passkeys"
    )
