"""Let the dealing desk buy and sell Treasury bonds at the custodian, held apart from bills.

Revision ID: 0532_dealing_bonds
Revises: 0531_payout_purpose

A dealing order now says what it deals: a bill of an investment product, as before, or a bond
of one issue in the bonds catalogue, named by the issue's code. Bonds go through the same two
people, the same lots and the same maturity run as bills, and are carried at cost in their own
asset, `dealing.bonds`, so the books and the client money check show them apart. What an order
deals is fixed when it is prepared: the orders guard already refuses any change to it.

The settlement and maturity checks of 0081 and 0082 read the asset that matches the order.
See docs/dealing-desk.md.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0532_dealing_bonds"
down_revision: str | None = "0531_payout_purpose"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADD = """
ALTER TABLE dealing_orders ADD COLUMN instrument varchar(8) NOT NULL DEFAULT 'bill'
    CONSTRAINT ck_dealing_orders_instrument CHECK (instrument IN ('bill', 'bond'))
"""

BILLS_FILTER = "WHERE account.code = 'dealing.bills'), 0)"
SETTLEMENT_FILTER = (
    "WHERE account.code = CASE o.instrument WHEN 'bond' THEN 'dealing.bonds' "
    "ELSE 'dealing.bills' END), 0)"
)
MATURITY_FILTER = "WHERE account.code = held), 0)"
MATURITY_DECLARE = (
    "        bills bigint;\n    BEGIN",
    "        bills bigint;\n        held text;\n    BEGIN",
)
# The asset the lot's purchase was posted to, read just before the entry's lines are.
MATURITY_HELD = (
    "        SELECT\n            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'bank.trust')",
    "        SELECT CASE bought.instrument WHEN 'bond' THEN 'dealing.bonds' ELSE 'dealing.bills' END\n"
    "            INTO held FROM public.dealing_orders AS bought WHERE bought.id = lot.order_id;\n"
    "        SELECT\n            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'bank.trust')",
)


def _source(name: str, attribute: str) -> str:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"finvest_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(getattr(module, attribute)).replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )


def _replaced(source: str, *pairs: tuple[str, str]) -> str:
    for old, new in pairs:
        if source.count(old) != 1:
            raise RuntimeError("The dealing function is not the one this revision extends")
        source = source.replace(old, new)
    return source


def settlement_matches() -> str:
    return _replaced(
        _source("0081_dealing_holdings", "SETTLEMENT_MATCHES"), (BILLS_FILTER, SETTLEMENT_FILTER)
    )


def maturity_guard() -> str:
    return _replaced(
        _source("0082_dealing_maturities", "MATURITY_GUARD"),
        MATURITY_DECLARE,
        MATURITY_HELD,
        (BILLS_FILTER, MATURITY_FILTER),
    )


def upgrade() -> None:
    op.execute(ADD)
    op.execute(settlement_matches())
    op.execute(maturity_guard())


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_orders WHERE instrument = 'bond') THEN
        RAISE EXCEPTION 'refusing to forget which dealing orders were for bonds';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(_source("0081_dealing_holdings", "SETTLEMENT_MATCHES"))
    op.execute(_source("0082_dealing_maturities", "MATURITY_GUARD"))
    op.execute("ALTER TABLE dealing_orders DROP COLUMN instrument")
