"""Hold "proved only while it is the same address" in the database, and fence the links.

Revision ID: 0163_identity_email_fence
Revises: 0162_identity_email_checks

Changing a client's email clears `email_verified_at`, whoever makes the change, so a new address
is never taken as proved. A link holds an email address, so a transaction sees only the links
of the client it acts for. Spending a link needs no session and runs inside
`identity_verify_email`, as the table owner.
"""

from alembic import op

revision = "0163_identity_email_fence"
down_revision = "0162_identity_email_checks"
branch_labels = None
depends_on = None

UPGRADE = [
    """
CREATE FUNCTION identity_clients_email_proof() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.email IS DISTINCT FROM OLD.email THEN
            NEW.email_verified_at := NULL;
        END IF;
        RETURN NEW;
    END;
    $$
""",
    "CREATE TRIGGER identity_clients_email_proof BEFORE UPDATE ON identity_clients "
    "FOR EACH ROW EXECUTE FUNCTION identity_clients_email_proof()",
    "ALTER TABLE identity_email_checks ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY identity_email_checks_own ON identity_email_checks TO finvest_app "
    "USING (public.finvest_actor_kind() = 'client' AND client_id = public.finvest_actor_id()) "
    "WITH CHECK (public.finvest_actor_kind() = 'client' "
    "AND client_id = public.finvest_actor_id())",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_email_checks) THEN
        RAISE EXCEPTION 'refusing to unfence email links that exist';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP POLICY identity_email_checks_own ON identity_email_checks")
    op.execute("ALTER TABLE identity_email_checks DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER identity_clients_email_proof ON identity_clients")
    op.execute("DROP FUNCTION identity_clients_email_proof()")
