"""Every route that decides another person's change asks for a recent second step."""

from typing import Any

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.app import create_app
from app.infrastructure.settings import Settings


class StubDatabase:
    async def ready(self) -> bool:
        return True

    async def close(self) -> None: ...


DECISIONS = {
    "POST /staff/team/changes/{change_id}/decision",
    "POST /staff/phone-changes/{change_id}/approval",
    "POST /staff/notification-wordings/proposals/{proposal_id}/decision",
    "POST /staff/announcements/{announcement_id}/decision",
    "POST /staff/email-suppressions/lifts/{lift_id}/decision",
    "POST /staff/complaints/{complaint_id}/outcome/decision",
    "POST /staff/identity-checks/{check_id}/approve",
    "POST /staff/identity-checks/{check_id}/reject",
    "POST /staff/payments/bank-transfers/{payment_id}/approve",
    "POST /staff/payout-destinations/{destination_id}/approve",
    "POST /staff/migration/control-totals/{totals_id}/confirmation",
    "POST /staff/migration/sign-offs/{sign_off_id}/decision",
    "POST /staff/rate-proposals/{proposal_id}/approve",
    "POST /staff/clients/locks/{lock_id}/decision",
}


def _routes(routes: list[Any], prefix: str = "") -> list[tuple[str, APIRoute]]:
    """Every route with its full path, through routers included in routers."""
    found: list[tuple[str, APIRoute]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append((prefix + route.path, route))
        elif hasattr(route, "original_router"):
            found += _routes(route.original_router.routes, prefix + route.include_context.prefix)
    return found


def _asks_again(dependant: Dependant) -> bool:
    return any(
        getattr(child.call, "__qualname__", "").startswith("require_confirmed.")
        or _asks_again(child)
        for child in dependant.dependencies
    )


def test_each_decision_route_needs_a_recent_second_step() -> None:
    settings = Settings(database_url="postgresql://finvest_app:unused@localhost/finvest")
    app = create_app(settings, StubDatabase())  # type: ignore[arg-type]
    routes = _routes(app.routes)
    assert len(routes) > 100
    confirmed = {
        f"{method} {path}"
        for path, route in routes
        if _asks_again(route.dependant)
        for method in route.methods or ()
    }
    assert confirmed == DECISIONS
