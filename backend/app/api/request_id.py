"""Give every request an ID, on the response and on every log line written while serving it."""

from contextvars import ContextVar

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.shared.push_ids import push_id

HEADER = "X-Request-ID"
_request_id: ContextVar[str | None] = ContextVar("finvest_request_id", default=None)


def current_request_id() -> str:
    """The ID of the request being served. Outside a request, a fresh one."""
    return _request_id.get() or push_id()


class RequestIdMiddleware:
    """Pure ASGI, so the ID is set before any other middleware or handler runs.

    The ID is always generated here. A client-supplied header is not trusted: it would let a
    caller write chosen text into the logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = push_id()
        token = _request_id.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            _request_id.reset(token)
