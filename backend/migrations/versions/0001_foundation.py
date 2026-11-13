"""Start the history: the push ID type and the actor context that row policies read."""

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL lets every role execute a new function unless told otherwise. Turn that off for
    # everything the migrator creates from now on, so each function names who may call it.
    op.execute("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
    # Every ID column uses this type. "C" collation keeps database ordering equal to the ASCII
    # ordering the IDs are generated in, whatever the database locale is.
    op.execute(
        """
        CREATE DOMAIN push_id AS text COLLATE "C"
          CONSTRAINT push_id_format CHECK (VALUE ~ '^[-0-9A-Z_a-z]{20}$')
        """
    )
    # The application sets these transaction-local settings (Database.actor_transaction). Row
    # policies call the functions. Anything unset or unrecognised is NULL, and a policy that
    # compares with NULL shows no rows, so a transaction with no actor fails closed.
    #
    # These settings are a fence against bugs, not a credential: any role can set them for
    # itself. So a policy that trusts the 'system' actor must be written TO finvest_worker only.
    # Written for finvest_app, it would let the API reach every row by naming itself the system.
    # tests/integration/test_foundation.py fails if such a policy exists.
    op.execute(
        """
        CREATE FUNCTION finvest_actor_kind() RETURNS text
        LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog
        AS $$
          SELECT CASE WHEN current_setting('finvest.actor_kind', true) IN ('client', 'staff', 'system')
                      THEN current_setting('finvest.actor_kind', true) END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION finvest_actor_id() RETURNS push_id
        LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $$
          SELECT CASE WHEN current_setting('finvest.actor_id', true) ~ '^[-0-9A-Z_a-z]{20}$'
                      THEN current_setting('finvest.actor_id', true)::push_id END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION finvest_actor_kind(), finvest_actor_id() FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION finvest_actor_kind(), finvest_actor_id() "
        "TO finvest_app, finvest_worker"
    )
    # Alembic created its version table as the migrator. The application roles have no use for it.
    op.execute("REVOKE ALL ON alembic_version FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION finvest_actor_id()")
    op.execute("DROP FUNCTION finvest_actor_kind()")
    op.execute("DROP DOMAIN push_id")
    op.execute("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
