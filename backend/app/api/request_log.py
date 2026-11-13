"""One log line per request: what was asked for, how it ended and how long it took.

The path is the route's template (`/client/passkeys/{passkey_id}`), never the path as sent, so
no identifier, phone number or other value a caller put in the URL reaches the logs. The query
string is never logged. A request that matched no route logs `unmatched`.
"""

import time

import structlog
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger()

# Probes from the uptime checker and the container health check would drown everything else.
QUIET_ROUTES = frozenset({"/health/live", "/health/ready"})


def route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None) if isinstance(route, BaseRoute) else None
    return path if isinstance(path, str) else "unmatched"


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def track(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, track)
        finally:
            route = route_template(scope)
            if not (route in QUIET_ROUTES and status < 500):
                logger.info(
                    "request_served",
                    method=scope["method"],
                    route=route,
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
