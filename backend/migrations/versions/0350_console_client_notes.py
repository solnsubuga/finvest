"""Keep staff notes on a client.

Revision ID: 0350_console_client_notes
Revises: 0270_payout_wallet_short

`console_client_notes` holds what one member of staff wrote about a client for the others, such
as "Called about a failed payout, resent it". A note is written by one person under their own
name and never changed or deleted: a mistake is put right by a later note. It moves nothing and
changes nothing about the client, so it is not a two-person step. Only staff read notes. A
client never sees them, and nothing in them reaches the client's data export.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0350_console_client_notes"
down_revision: str | None = "0270_payout_wallet_short"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
MAX_BODY = 1000

TABLE = f"""
CREATE TABLE console_client_notes (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    body varchar({MAX_BODY}) NOT NULL,
    written_by push_id NOT NULL,
    written_at timestamptz NOT NULL,
    CONSTRAINT pk_console_client_notes PRIMARY KEY (id),
    CONSTRAINT fk_console_client_notes_client_id_identity_clients
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT fk_console_client_notes_written_by_identity_staff
        FOREIGN KEY (written_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_console_client_notes_body CHECK (btrim(body) <> '')
)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
_STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    "CREATE INDEX ix_console_client_notes_client "
    "ON console_client_notes (client_id, written_at DESC, id DESC)",
    "REVOKE ALL ON console_client_notes FROM PUBLIC",
    # No UPDATE or DELETE for anyone: a note is kept as written.
    f"GRANT SELECT, INSERT ON console_client_notes TO {APP_ROLE}",
    "ALTER TABLE console_client_notes ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY console_client_notes_read ON console_client_notes FOR SELECT TO {APP_ROLE} "
    f"USING ({_STAFF})",
    # Written only under the acting member of staff's own name, while they are active.
    f"CREATE POLICY console_client_notes_insert ON console_client_notes FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({_STAFF} AND written_by = finvest_actor_id() AND EXISTS ("
    "SELECT FROM identity_staff WHERE id = finvest_actor_id() AND status = 'active'))",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM console_client_notes) THEN
        RAISE EXCEPTION 'refusing to drop staff notes that hold records';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(TABLE)
    for statement in RULES:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE console_client_notes")
