"""Let staff open a help chat with a client.

Revision ID: 0332_support_staff_opens_chat
Revises: 0331_correction_pay_out_held

Until now only a client opened a chat, and staff could only answer one. When a term's money
waits because its payout could not be made, the Maturities screen asks staff to message the
client, so staff now open the client's chat when there is none. It is still one open chat per
client: a staff member writes into the open one if there is one. A chat staff open starts not
waiting for staff, because nobody has asked anything yet. See docs/support.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0332_support_staff_opens_chat"
down_revision: str | None = "0331_correction_pay_out_held"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
_OWN = "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()"
_STAFF = "finvest_actor_kind() = 'staff'"


def upgrade() -> None:
    op.execute("DROP POLICY support_conversations_insert ON support_conversations")
    # A new chat is open and nobody has read or solved it. Staff open one waiting for nobody.
    op.execute(
        f"CREATE POLICY support_conversations_insert ON support_conversations FOR INSERT "
        f"TO {APP_ROLE} WITH CHECK (status = 'open' AND (({_OWN}) OR ({_STAFF} "
        "AND waiting_since IS NULL AND solved_at IS NULL AND client_read_at IS NULL)))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY support_conversations_insert ON support_conversations")
    op.execute(
        f"CREATE POLICY support_conversations_insert ON support_conversations FOR INSERT "
        f"TO {APP_ROLE} WITH CHECK ({_OWN} AND status = 'open')"
    )
