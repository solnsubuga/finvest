"""Start the built-in Treasury bill type at Bank of Uganda's 20% withholding tax.

Revision ID: 0530_treasury_bill_type_tax
Revises: 0496_bond_sale_unsettled_signal

Migration 0493 seeded the built-in types with the tax the system used before, 15%, on bills as
well as fixed income. Bank of Uganda withholds 20% on Treasury bills, and every bill staff have
set up is at 20%, so a new bill started from the type's usual tax had to be corrected by hand.
The built-in Treasury bill type now starts at 20%. Fixed income stays at 15% and the Treasury
bond type at 20%.

A type's usual tax only fills the tax field of a new product. Products and rates already set up
keep their own tax, so nothing a client holds changes. The version moves on, so an edit to the
type proposed against the old figure is refused when approved and is proposed again.

This runs as the migration owner: built-in types have no change behind them, and the guard in
0493 only holds the application and worker roles to approved changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0530_treasury_bill_type_tax"
down_revision: str | None = "0496_bond_sale_unsettled_signal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BILL_TYPE = "-PTYPE0000000000000B"


def _set(from_bps: int, to_bps: int) -> None:
    op.execute(
        f"UPDATE product_types SET default_tax_bps = {to_bps}, version = version + 1, "  # noqa: S608
        f"updated_at = now() WHERE id = '{BILL_TYPE}' AND built_in "
        f"AND default_tax_bps = {from_bps}"
    )


def upgrade() -> None:
    # Only while it is still the seeded figure: a type staff already changed keeps theirs.
    _set(1500, 2000)


def downgrade() -> None:
    _set(2000, 1500)
