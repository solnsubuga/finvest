"""Closing a month is guarded by a permission that changes something, not by seeing Finance.

A later read-only finance role holds `finance.view`, so it must never be able to propose,
decline, withdraw or approve a month close.
"""

from fastapi.dependencies.models import Dependant

from app.api.app import create_app
from app.infrastructure.settings import Settings
from app.modules.identity.domain.permissions import ROLES, allows
from tests.unit.test_confirmed_approvals import StubDatabase, _routes

MONTH_CLOSE_WRITES = {
    "POST /staff/finance/month-closes",
    "POST /staff/finance/month-closes/{close_id}/approve",
    "POST /staff/finance/month-closes/{close_id}/decline",
    "POST /staff/finance/month-closes/{close_id}/withdraw",
}


def _permissions(dependant: Dependant) -> set[str]:
    """The permissions a route's `require_permission` or `require_confirmed` checks."""
    found: set[str] = set()
    for child in dependant.dependencies:
        qualname = getattr(child.call, "__qualname__", "")
        if qualname.startswith(("require_permission.", "require_confirmed.")):
            cells = getattr(child.call, "__closure__", None) or ()
            found |= {cell.cell_contents for cell in cells if isinstance(cell.cell_contents, str)}
        found |= _permissions(child)
    return found


def test_every_month_close_write_needs_finance_close() -> None:
    settings = Settings(database_url="postgresql://finvest_app:unused@localhost/finvest")
    app = create_app(settings, StubDatabase())  # type: ignore[arg-type]
    declared = {
        f"{method} {path}": _permissions(route.dependant)
        for path, route in _routes(app.routes)
        for method in route.methods or ()
        if f"{method} {path}" in MONTH_CLOSE_WRITES
    }
    assert set(declared) == MONTH_CLOSE_WRITES
    for route, permissions in declared.items():
        assert permissions == {"finance.close"}, route
    listed = {
        f"{method} {path}": _permissions(route.dependant)
        for path, route in _routes(app.routes)
        for method in route.methods or ()
        if path == "/staff/finance/month-closes" and method == "GET"
    }
    assert listed == {"GET /staff/finance/month-closes": {"finance.view"}}


def test_only_admin_and_finance_close_months() -> None:
    assert {role for role in ROLES if allows(role, "finance.close")} == {"admin", "finance"}
    for role in ROLES:
        if allows(role, "finance.close"):
            assert allows(role, "finance.view")
