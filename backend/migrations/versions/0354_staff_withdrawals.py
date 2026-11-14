"""Let two members of staff start an early withdrawal for a client who asked in writing.

Revision ID: 0354_staff_withdrawals
Revises: 0353_documents_staff_statements

The owner decided (2026-11-14): staff may start a withdrawal for a client only when the client
asked for it in their own help chat. One member of staff proposes it citing that message, a
different one approves, the money goes only to an approved payout account of the client's own,
and the client is told by SMS and email.

* `console_client_actions` gains the kind `withdrawal`, whose details name the investment, the
  payout account and the client's message. One message backs one withdrawal: a second proposal
  citing it is refused while the first waits or after it was approved.
* `investments` gains a staff update policy, and its guard (last replaced in 0241) gains one
  exception to "only the client asks to withdraw": a member of staff may record the request for
  exactly the investment and payout account a `withdrawal` action approved in this same
  transaction names. Everything else the guard checks still holds.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0354_staff_withdrawals"
down_revision: str | None = "0353_documents_staff_statements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"

# The actor settings are a fence against bugs, not a credential (see migration 0001).
_APPROVED = (
    "EXISTS (SELECT FROM public.console_client_actions a WHERE a.kind = 'withdrawal' "
    "AND a.client_id = {client} AND a.details ->> 'investment_id' = {investment} "
    "AND a.status = 'approved' AND a.decided_in = pg_current_xact_id(){extra})"
)
_CLAUSE = "IF actor IS DISTINCT FROM 'client' OR OLD.status <> 'active'"
_STAFF_CLAUSE = (
    "IF (actor IS DISTINCT FROM 'client' AND NOT (actor = 'staff' AND "
    + _APPROVED.format(
        client="OLD.client_id",
        investment="OLD.id",
        extra=" AND a.details ->> 'destination_id' = NEW.withdrawal_destination_id",
    )
    + ")) OR OLD.status <> 'active'"
)

KINDS = [
    "ALTER TABLE console_client_actions DROP CONSTRAINT ck_console_client_actions_kind",
    "ALTER TABLE console_client_actions ADD CONSTRAINT ck_console_client_actions_kind "
    "CHECK (kind IN ('pin_reset', 'withdrawal_limits', 'statement', 'withdrawal'))",
    "ALTER TABLE console_client_actions ADD CONSTRAINT ck_console_client_actions_withdrawal "
    "CHECK (kind <> 'withdrawal' OR (details ? 'investment_id' AND details ? 'destination_id' "
    "AND details ? 'message_id'))",
    "CREATE UNIQUE INDEX uq_console_client_actions_withdrawal_message "
    "ON console_client_actions ((details ->> 'message_id')) "
    "WHERE kind = 'withdrawal' AND status IN ('proposed', 'approved')",
    f"CREATE POLICY investments_staff_withdrawal ON investments FOR UPDATE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff' AND "
    + _APPROVED.format(client="investments.client_id", investment="investments.id", extra="")
    + ")",
]


def _guard(revision_id: str) -> str:
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to read the guard")
    guard: str = script.get_revision(revision_id).module.GUARD
    return guard


def upgrade() -> None:
    for statement in KINDS:
        op.execute(statement)
    guard = _guard("0241_investment_transfers")
    if guard.count(_CLAUSE) != 1:
        raise RuntimeError("the investments guard no longer has the withdrawal clause")
    op.execute(guard.replace(_CLAUSE, _STAFF_CLAUSE))


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM console_client_actions WHERE kind = 'withdrawal') THEN
        RAISE EXCEPTION 'refusing to drop withdrawals staff started that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(_guard("0241_investment_transfers"))
    op.execute("DROP POLICY investments_staff_withdrawal ON investments")
    op.execute("DROP INDEX uq_console_client_actions_withdrawal_message")
    op.execute(
        "ALTER TABLE console_client_actions DROP CONSTRAINT ck_console_client_actions_withdrawal"
    )
    op.execute("ALTER TABLE console_client_actions DROP CONSTRAINT ck_console_client_actions_kind")
    op.execute(
        "ALTER TABLE console_client_actions ADD CONSTRAINT ck_console_client_actions_kind "
        "CHECK (kind IN ('pin_reset', 'withdrawal_limits', 'statement'))"
    )
