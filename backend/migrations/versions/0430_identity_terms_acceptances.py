"""Record which terms and privacy notice each client agreed to, and when.

Revision ID: 0430_identity_terms_acceptances
Revises: 0372_kyc_liveness_transactions

`identity_terms_acceptances` holds one row per document a client agreed to: the terms, or the
privacy notice, with the version (the day its wording took effect) and how they agreed, at
sign-up or when claiming an account moved over from paper records. The API writes the rows in
the transaction that creates or claims the account, before anyone is signed in, so inserting
needs no actor, as for the account itself. A row is never changed or deleted. A client reads
their own, and staff read every client's. See docs/documents.md#terms-you-accepted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0430_identity_terms_acceptances"
down_revision: str | None = "0372_kyc_liveness_transactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"

TABLE = """
CREATE TABLE identity_terms_acceptances (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    document varchar(16) NOT NULL,
    version char(10) COLLATE "C" NOT NULL,
    via varchar(16) NOT NULL,
    accepted_at timestamptz NOT NULL,
    CONSTRAINT pk_identity_terms_acceptances PRIMARY KEY (id),
    CONSTRAINT fk_identity_terms_acceptances_client
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT ck_identity_terms_acceptances_document CHECK (document IN ('terms', 'privacy')),
    CONSTRAINT ck_identity_terms_acceptances_version
        CHECK (version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    CONSTRAINT ck_identity_terms_acceptances_via CHECK (via IN ('signup', 'claim')),
    -- Agreeing to the same version twice says nothing new.
    CONSTRAINT uq_identity_terms_acceptances UNIQUE (client_id, document, version)
)
"""

RULES = [
    "REVOKE ALL ON identity_terms_acceptances FROM PUBLIC",
    # No UPDATE or DELETE for anyone: an agreement is a record.
    f"GRANT SELECT, INSERT ON identity_terms_acceptances TO {APP_ROLE}",
    "ALTER TABLE identity_terms_acceptances ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY identity_terms_acceptances_read ON identity_terms_acceptances FOR SELECT "
    f"TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    # Written with no actor, in the transaction that creates or claims the account. A signed-in
    # client or a member of staff never writes one.
    f"CREATE POLICY identity_terms_acceptances_insert ON identity_terms_acceptances FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() IS NULL)",
]


def upgrade() -> None:
    op.execute(TABLE)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_terms_acceptances) THEN
        RAISE EXCEPTION 'refusing to drop terms acceptances';
    END IF;
END
$$
"""


def downgrade() -> None:
    # What a client agreed to is a record. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE identity_terms_acceptances")
