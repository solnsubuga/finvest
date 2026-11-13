"""Which device the current request comes from, as a fixed label, for the session it opens."""

from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

from app.modules.identity.domain.devices import device_label

current_device: ContextVar[str | None] = ContextVar("current_device", default=None)


class DeviceMiddleware:
    """Label each HTTP request's device from its User-Agent, for the rest of that request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        agent = next(
            (value.decode("latin-1") for name, value in scope["headers"] if name == b"user-agent"),
            None,
        )
        token = current_device.set(device_label(agent))
        try:
            await self.app(scope, receive, send)
        finally:
            current_device.reset(token)
