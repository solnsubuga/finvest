"""Raise bond sales approved but still not paid on /health/operations.

Revision ID: 0496_bond_sale_unsettled_signal
Revises: 0495_bond_sales

Approving a sale queues `bonds.settle_sale`, and the worker pays it within moments. A refusal
fails the job for good and raises `jobs_failed`, but only for 24 hours, while the sale stays
approved with nothing paid. `finvest_operations_signals` gains `bond_sales_unsettled`: sales
approved more than an hour ago that are still approved. `GET /health/operations` answers 503
while it is above zero, so the uptime alert reaches a person until the sale is paid. The count
is live, so it falls back to zero as soon as the worker pays each one.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0496_bond_sale_unsettled_signal"
down_revision: str | None = "0495_bond_sales"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"finvest_{name}", Path(__file__).with_name(f"{name}.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_previous = _load("0271_interest_behind_signal")
BEFORE: str = _previous.BEFORE + _previous.BEHIND

UNSETTLED = """
        UNION ALL
        SELECT 'bond_sales_unsettled', count(*) FROM public.bond_sales
            WHERE status = 'approved' AND decided_at < p_now - interval '1 hour'
"""


def upgrade() -> None:
    op.execute(_previous._function(BEFORE + UNSETTLED))


def downgrade() -> None:
    # Changes a read-only count and drops no data.
    op.execute(_previous._function(BEFORE))
