"""A signed-in client changes their own mobile number and email address.

Revision ID: 0236_client_own_changes
Revises: 0310_identity_unlock_keys

The API holds no UPDATE on `identity_clients.phone` or `.email` (migration 0008), and keeps none.
A client changes them only through two SECURITY DEFINER functions that take the client from
the actor settings, so a client changes only their own account:

* `identity_codes` takes the purpose `phone_change`. Such a code belongs to one client
  (`actor_id`), the session that asked (`bound_to`) and the new number it was texted to
  (`phone`).
* `identity_change_own_phone` moves the number only to the one held by a `phone_change` code
  this client spent in this same transaction. It limits withdrawals to known accounts for a
  day, ends the client's other sessions, voids codes already sent and returns the old number,
  so the caller can tell the client there.
* A client's session may now record `reverified_at`, when they confirm it is them with a passkey
  or authenticator app again, as staff do before an approval (migration 0165).
* `identity_change_own_email` sets a new address. The trigger from migration 0163 clears the
  proof, so a new address is never taken as proved. It returns the old address.

Asking for a passkey or authenticator app first is the application's check (see
docs/sign-in.md). The actor settings are a fence against bugs, not a credential (migration 0001).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0236_client_own_changes"
down_revision: str | None = "0310_identity_unlock_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# Adds a code purpose, two functions and widens a check: the previous release keeps working.
zero_downtime = True

APP_ROLE = "finvest_app"

PURPOSES = [
    "ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_purpose",
    "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_purpose CHECK (purpose IN "
    "('signup', 'login', 'password_reset', 'claim', 'payout_account', 'phone_change'))",
    "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_phone_change CHECK ("
    "purpose <> 'phone_change' OR "
    "(actor_id IS NOT NULL AND bound_to IS NOT NULL AND phone IS NOT NULL))",
    # The daily ceiling counts one client's number change codes.
    "CREATE INDEX ix_identity_codes_phone_change ON identity_codes (actor_id, created_at) "
    "WHERE purpose = 'phone_change'",
]

REVERIFIED = [
    "ALTER TABLE identity_sessions DROP CONSTRAINT ck_identity_sessions_reverified",
    "ALTER TABLE identity_sessions ADD CONSTRAINT ck_identity_sessions_reverified "
    "CHECK (reverified_at IS NULL OR reverified_at >= created_at)",
]
OLD_REVERIFIED = [
    "ALTER TABLE identity_sessions DROP CONSTRAINT ck_identity_sessions_reverified",
    "ALTER TABLE identity_sessions ADD CONSTRAINT ck_identity_sessions_reverified "
    "CHECK (reverified_at IS NULL OR (actor_kind = 'staff' AND reverified_at >= created_at))",
]

PHONE = """
CREATE FUNCTION identity_change_own_phone(
    p_code_id text, p_session_id text, p_restricted_until timestamptz
)
    RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_client text := public.finvest_actor_id();
        v_code public.identity_codes%ROWTYPE;
        v_old text;
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'client' THEN
            RAISE EXCEPTION 'only a client changes their own number' USING ERRCODE = '42501';
        END IF;
        -- Spent by this client in this transaction, so a code cannot be replayed later.
        SELECT * INTO v_code FROM public.identity_codes
         WHERE id = p_code_id AND purpose = 'phone_change' AND actor_id = v_client
           AND consumed_at IS NOT NULL AND xmin = pg_current_xact_id()::xid
           FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'a number changes only with its code spent in the same transaction'
                USING ERRCODE = '42501';
        END IF;
        SELECT phone INTO v_old FROM public.identity_clients
         WHERE id = v_client AND status = 'active' FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'the client is not active' USING ERRCODE = '42501';
        END IF;
        UPDATE public.identity_clients
           SET phone = v_code.phone,
               restricted_until = greatest(coalesce(restricted_until, p_restricted_until),
                                           p_restricted_until)
         WHERE id = v_client;
        UPDATE public.identity_sessions
           SET revoked_at = v_code.consumed_at, revoked_reason = 'phone_changed'
         WHERE actor_kind = 'client' AND actor_id = v_client AND revoked_at IS NULL
           AND id IS DISTINCT FROM p_session_id;
        UPDATE public.identity_codes SET consumed_at = v_code.consumed_at
         WHERE actor_id = v_client AND consumed_at IS NULL;
        RETURN v_old;
    END;
    $$
"""

EMAIL = """
CREATE FUNCTION identity_change_own_email(p_email text)
    RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_client text := public.finvest_actor_id();
        v_old text;
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'client' THEN
            RAISE EXCEPTION 'only a client changes their own email' USING ERRCODE = '42501';
        END IF;
        SELECT email INTO v_old FROM public.identity_clients
         WHERE id = v_client AND status = 'active' FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'the client is not active' USING ERRCODE = '42501';
        END IF;
        -- The trigger from migration 0163 clears email_verified_at when the address changes.
        UPDATE public.identity_clients SET email = p_email WHERE id = v_client;
        RETURN v_old;
    END;
    $$
"""

FUNCTIONS = (
    "identity_change_own_phone(text, text, timestamptz)",
    "identity_change_own_email(text)",
)


def upgrade() -> None:
    for statement in [*PURPOSES, *REVERIFIED]:
        op.execute(statement)
    op.execute(PHONE)
    op.execute(EMAIL)
    for function in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {APP_ROLE}")


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_codes WHERE purpose = 'phone_change') THEN
        RAISE EXCEPTION 'refusing to drop number change codes';
    END IF;
    IF EXISTS (SELECT FROM identity_sessions
                WHERE actor_kind = 'client' AND reverified_at IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop client re-confirmations';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in OLD_REVERIFIED:
        op.execute(statement)
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
    op.execute("DROP INDEX ix_identity_codes_phone_change")
    op.execute("ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_phone_change")
    op.execute("ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_purpose")
    op.execute(
        "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_purpose "
        "CHECK (purpose IN ('signup', 'login', 'password_reset', 'claim', 'payout_account'))"
    )
