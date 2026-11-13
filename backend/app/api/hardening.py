"""Response headers and a request size limit that hold whatever sits in front of the API.

The edge (Caddy behind Cloudflare) sets most of these headers too. They are repeated here so a
response stays safe in staging, in local runs and if the edge configuration ever changes.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import envelope

# The API answers JSON only. Nothing it returns is cached, sniffed, framed or allowed to run.
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-site",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

# Every request body is JSON, a few kilobytes at most. A route that takes a file names its path
# prefix here with its own limit, so a large body is never read anywhere else.
DEFAULT_BODY_LIMIT = 256 * 1024
# A `*` stands for one path segment, such as an ID: `/client/investments/*/payments/`.
BODY_LIMITS: dict[str, int] = {
    # A bank transfer's receipt: at most 10 MiB, plus the form fields and multipart framing.
    # The edge allows 12 MiB on the same path.
    "/client/investments/*/payments/bank-transfer": 11 * 1024 * 1024,
    # An identity document photo: at most 10 MiB, plus the multipart framing. The edge allows
    # 12 MiB on the same path.
    "/client/identity-check/photos/": 11 * 1024 * 1024,
}


def _matches(prefix: str, path: str) -> bool:
    """Whether `path` starts with `prefix`, where a `*` in `prefix` is any one segment."""
    if "*" not in prefix:
        return path.startswith(prefix)
    wanted = prefix.split("/")
    given = path.split("/")
    if len(given) < len(wanted):
        return False
    for index, part in enumerate(wanted):
        last = index == len(wanted) - 1
        if part == "*":
            if not given[index]:
                return False
        elif last:
            if not given[index].startswith(part):
                return False
        elif given[index] != part:
            return False
    return True


def body_limit(path: str) -> int:
    longest = max((prefix for prefix in BODY_LIMITS if _matches(prefix, path)), key=len, default="")
    return BODY_LIMITS.get(longest, DEFAULT_BODY_LIMIT)


class SecurityHeadersMiddleware:
    """Adds `HEADERS` to every response, keeping any value a route set on purpose."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _TooLarge(HTTPException):
    """Raised from `receive`, so FastAPI's handlers answer it as `payload_too_large`."""

    def __init__(self) -> None:
        super().__init__(status_code=413)


class BodyLimitMiddleware:
    """Refuses a body over the limit for its path with `payload_too_large` (413).

    A declared Content-Length over the limit is refused before anything is read. A body sent
    without one, or longer than it said, is counted as it arrives and cut off at the limit.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit(scope["path"])
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await envelope("payload_too_large")(scope, receive, send)
            return
        received = 0
        started = False

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        async def track(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, track)
        except _TooLarge:
            if started:
                raise
            await envelope("payload_too_large")(scope, receive, send)
