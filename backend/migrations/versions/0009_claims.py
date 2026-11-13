"""Let a client carried over from Finvest 1.0 exist before they choose a password.

Revision ID: 0009_claims
Revises: 0111_goal_rollovers
"""

from alembic import op

revision = "0009_claims"
down_revision = "0111_goal_rollovers"
branch_labels = None
depends_on = None

# An imported client is 'unclaimed': they have a number and maybe an email, but no password,
# so nothing can sign in as them. They claim the account by proving the number with a code and
# choosing a password, which makes them 'active'. An account never goes back to unclaimed.
UPGRADE = [
    "ALTER TABLE identity_clients ALTER COLUMN password_hash DROP NOT NULL",
    "ALTER TABLE identity_clients ALTER COLUMN password_changed_at DROP NOT NULL",
    "ALTER TABLE identity_clients DROP CONSTRAINT ck_identity_clients_status",
    "ALTER TABLE identity_clients ADD CONSTRAINT ck_identity_clients_status "
    "CHECK (status IN ('unclaimed', 'active', 'locked', 'closed'))",
    "ALTER TABLE identity_clients ADD CONSTRAINT ck_identity_clients_password "
    "CHECK ((status = 'unclaimed') = (password_hash IS NULL) "
    "AND (password_hash IS NULL) = (password_changed_at IS NULL))",
    """
CREATE FUNCTION identity_clients_never_unclaimed_again() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'unclaimed' AND OLD.status <> 'unclaimed' THEN
        RAISE EXCEPTION 'a claimed account cannot become unclaimed again';
    END IF;
    RETURN NEW;
END
$$
""",
    "CREATE TRIGGER tr_identity_clients_never_unclaimed_again BEFORE UPDATE OF status "
    "ON identity_clients FOR EACH ROW EXECUTE FUNCTION identity_clients_never_unclaimed_again()",
    "ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_purpose",
    "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_purpose "
    "CHECK (purpose IN ('signup', 'login', 'password_reset', 'claim'))",
    "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_claim "
    "CHECK (purpose <> 'claim' OR actor_id IS NOT NULL)",
    # The API may not change a client's status (0008). Claiming is the one change it makes, so
    # it goes through this function, which does only that: an unclaimed account gets its first
    # password and becomes active, and only in the transaction that spent a claim code for that
    # account, so no other API path can set a first password. It runs as the table owner and
    # pins its search path.
    """
CREATE FUNCTION identity_claim_client(
    p_id text, p_code_id text, p_password_hash text, p_now timestamptz,
    p_restricted_until timestamptz
) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    WITH spent AS (
        SELECT FROM public.identity_codes
        WHERE id = p_code_id AND purpose = 'claim' AND actor_id = p_id
            AND consumed_at IS NOT NULL AND xmin = pg_current_xact_id()::xid
    ), claimed AS (
        UPDATE public.identity_clients
        SET password_hash = p_password_hash, password_changed_at = p_now, status = 'active',
            restricted_until = greatest(coalesce(restricted_until, p_restricted_until),
                                        p_restricted_until)
        WHERE id = p_id AND status = 'unclaimed' AND password_hash IS NULL
            AND EXISTS (SELECT FROM spent)
        RETURNING 1
    )
    SELECT EXISTS (SELECT FROM claimed)
    $$
""",
    "REVOKE ALL ON FUNCTION identity_claim_client(text, text, text, timestamptz, timestamptz) "
    "FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION identity_claim_client(text, text, text, timestamptz, timestamptz) "
    "TO finvest_app",
]

DOWNGRADE = [
    "DROP FUNCTION identity_claim_client(text, text, text, timestamptz, timestamptz)",
    "ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_claim",
    "ALTER TABLE identity_codes DROP CONSTRAINT ck_identity_codes_purpose",
    "ALTER TABLE identity_codes ADD CONSTRAINT ck_identity_codes_purpose "
    "CHECK (purpose IN ('signup', 'login', 'password_reset'))",
    "DROP TRIGGER tr_identity_clients_never_unclaimed_again ON identity_clients",
    "DROP FUNCTION identity_clients_never_unclaimed_again()",
    "ALTER TABLE identity_clients DROP CONSTRAINT ck_identity_clients_password",
    "ALTER TABLE identity_clients DROP CONSTRAINT ck_identity_clients_status",
    "ALTER TABLE identity_clients ADD CONSTRAINT ck_identity_clients_status "
    "CHECK (status IN ('active', 'locked', 'closed'))",
    "ALTER TABLE identity_clients ALTER COLUMN password_changed_at SET NOT NULL",
    "ALTER TABLE identity_clients ALTER COLUMN password_hash SET NOT NULL",
]

# An unclaimed account has no password, so the old columns cannot hold it. Refuse rather than
# lose the client or the record of a claim code.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_clients WHERE status = 'unclaimed')
        OR EXISTS (SELECT FROM identity_codes WHERE purpose = 'claim') THEN
        RAISE EXCEPTION 'refusing to drop unclaimed accounts or claim codes';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in DOWNGRADE:
        op.execute(statement)
