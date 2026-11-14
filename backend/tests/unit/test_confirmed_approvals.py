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
    "POST /staff/claim-checks/{claim_check_id}/match",
    "POST /staff/claim-checks/{claim_check_id}/no-match",
    "POST /staff/payments/bank-transfers/{payment_id}/approve",
    "POST /staff/payments/reassignments/{reassignment_id}/approve",
    "POST /staff/payout-destinations/{destination_id}/approve",
    "POST /staff/payout-resolutions/{resolution_id}/approve",
    "POST /staff/payout-releases/{release_id}/approve",
    "POST /staff/payouts/{payout_id}/decline",
    "POST /staff/reconciliation/days/{day}/approve",
    "POST /staff/migration/control-totals/{totals_id}/confirmation",
    "POST /staff/migration/sign-offs/{sign_off_id}/decision",
    "POST /staff/rate-proposals/{proposal_id}/approve",
    "POST /staff/investment-corrections/{correction_id}/approve",
    "POST /staff/clients/locks/{lock_id}/decision",
    "POST /staff/dealing/orders/{order_id}/approve",
    "POST /staff/dealing/orders/{order_id}/confirm-result",
    "POST /staff/dealing/buffer/rules/{proposal_id}/approve",
    "POST /staff/dealing/buffer/capital/{movement_id}/approve",
    "POST /staff/tax/remittances/{remittance_id}/approve",
}
# A staff route whose path ends like this approves or decides something, so it must ask.
DECIDING_ENDINGS = ("approve", "approval", "decision", "confirmation", "confirm-result")


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
    # Found without the list, so the next module's approval route fails here until it asks.
    deciding = {
        f"{method} {path}"
        for path, route in routes
        if path.startswith("/staff/") and path.rstrip("/").endswith(DECIDING_ENDINGS)
        for method in route.methods or ()
        if method == "POST"
    }
    assert deciding <= confirmed, sorted(deciding - confirmed)
