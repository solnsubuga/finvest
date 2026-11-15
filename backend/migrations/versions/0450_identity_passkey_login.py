"""Let a client log in with a passkey alone, without typing a number or password first.

Revision ID: 0450_identity_passkey_login
Revises: 0440_sms_delivery_reports

Owned by the identity module. See "Logging in with a passkey" in docs/sign-in.md.

A passkey log in asks the device for any Finvest passkey it holds, so when the challenge is
made nobody knows yet whose passkey will answer it. Its challenge is the one kind with no
person: purpose `passkey_login`, always a client's, and `actor_id` empty. Every other purpose
still names its person. `bound_to` is the log in attempt the app was given with the challenge.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0450_identity_passkey_login"
down_revision: str | None = "0440_sms_delivery_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_passkey_challenges WHERE purpose = 'passkey_login') THEN
        RAISE EXCEPTION 'refusing to drop passkey log in challenges';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(
        "ALTER TABLE identity_passkey_challenges "
        "DROP CONSTRAINT ck_identity_passkey_challenges_purpose"
    )
    op.execute(
        "ALTER TABLE identity_passkey_challenges ADD CONSTRAINT "
        "ck_identity_passkey_challenges_purpose CHECK (purpose IN ('register', 'sign_in', "
        "'reverify', 'passkey_login'))"
    )
    op.execute("ALTER TABLE identity_passkey_challenges ALTER COLUMN actor_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE identity_passkey_challenges ADD CONSTRAINT "
        "ck_identity_passkey_challenges_person CHECK ((purpose = 'passkey_login') = "
        "(actor_id IS NULL) AND (purpose <> 'passkey_login' OR actor_kind = 'client'))"
    )


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(
        "ALTER TABLE identity_passkey_challenges DROP CONSTRAINT ck_identity_passkey_challenges_person"
    )
    op.execute("ALTER TABLE identity_passkey_challenges ALTER COLUMN actor_id SET NOT NULL")
    op.execute(
        "ALTER TABLE identity_passkey_challenges "
        "DROP CONSTRAINT ck_identity_passkey_challenges_purpose"
    )
    op.execute(
        "ALTER TABLE identity_passkey_challenges ADD CONSTRAINT "
        "ck_identity_passkey_challenges_purpose CHECK (purpose IN ('register', 'sign_in', "
        "'reverify'))"
    )
