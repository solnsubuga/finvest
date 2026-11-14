"""Let two staff send on the money a matured investment holds for the client.

Revision ID: 0331_correction_pay_out_held
Revises: 0330_investment_run_requests

A matured investment whose payout could not be made waits with its money in the client's payout
pending account until the client chooses where to send it. When the client asks on the phone
instead, staff now do it for them as a correction of a new kind, `pay_out`: one member of staff
proposes one of the client's own approved payout accounts with a reason, a different one
approves, and the worker creates the payout exactly as the client's own request would, under
the same key. The money stays the client's and goes only to an account of theirs.

The correction moves no money in this module, so a done `pay_out` has a payout and no entry of
its own: the payout's reserve entry carries the money. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0331_correction_pay_out_held"
down_revision: str | None = "0330_investment_run_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    "ALTER TABLE investment_corrections DROP CONSTRAINT ck_investment_corrections_kind",
    "ALTER TABLE investment_corrections ADD CONSTRAINT ck_investment_corrections_kind "
    "CHECK (kind IN ('unwind', 'refund', 'pay_out'))",
    "ALTER TABLE investment_corrections DROP CONSTRAINT ck_investment_corrections_destination",
    "ALTER TABLE investment_corrections ADD CONSTRAINT ck_investment_corrections_destination "
    "CHECK ((kind IN ('refund', 'pay_out')) = (destination_id IS NOT NULL))",
    "ALTER TABLE investment_corrections DROP CONSTRAINT ck_investment_corrections_outcome",
    # Done means the money moved: an unwind or a refund by its own entry, and a refund or a
    # pay out by its payout. A pay out never has an entry here. Failed means nothing moved.
    "ALTER TABLE investment_corrections ADD CONSTRAINT ck_investment_corrections_outcome CHECK ("
    "(status IN ('done', 'failed')) = (done_at IS NOT NULL) "
    "AND (status = 'failed') = (failure_code IS NOT NULL) "
    "AND (kind = 'pay_out' OR (status = 'done') = (entry_id IS NOT NULL)) "
    "AND (kind <> 'pay_out' OR entry_id IS NULL) "
    "AND (payout_id IS NULL OR (status = 'done' AND kind IN ('refund', 'pay_out'))) "
    "AND (status <> 'done' OR kind = 'unwind' OR payout_id IS NOT NULL))",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_corrections WHERE kind = 'pay_out') THEN
        RAISE EXCEPTION 'refusing to drop the record of held money staff sent on';
    END IF;
END
$$
"""

DOWNGRADE = [
    "ALTER TABLE investment_corrections DROP CONSTRAINT ck_investment_corrections_outcome",
    "ALTER TABLE investment_corrections ADD CONSTRAINT ck_investment_corrections_outcome CHECK ("
    "(status IN ('done', 'failed')) = (done_at IS NOT NULL) "
    "AND (status = 'done') = (entry_id IS NOT NULL) "
    "AND (status = 'failed') = (failure_code IS NOT NULL) "
    "AND (payout_id IS NULL OR (status = 'done' AND kind = 'refund')) "
    "AND (status <> 'done' OR kind <> 'refund' OR payout_id IS NOT NULL))",
    "ALTER TABLE investment_corrections DROP CONSTRAINT ck_investment_corrections_destination",
    "ALTER TABLE investment_corrections ADD CONSTRAINT ck_investment_corrections_destination "
    "CHECK ((kind = 'refund') = (destination_id IS NOT NULL))",
    "ALTER TABLE investment_corrections DROP CONSTRAINT ck_investment_corrections_kind",
    "ALTER TABLE investment_corrections ADD CONSTRAINT ck_investment_corrections_kind "
    "CHECK (kind IN ('unwind', 'refund'))",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in DOWNGRADE:
        op.execute(statement)
