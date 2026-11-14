"""Send through a first provider, and through a second only when the first did not take it.

Africa's Talking is the first provider and ioTec the second (docs/decisions.md, 2026-11-14).

The second provider is tried only when the first says, for certain, that it did not take the
message: it could not be reached, turned the request away, or refused it. When the first may
have taken it (`SmsUncertain`), the message is not sent again anywhere, because the person
could get it twice. That is the same rule every provider call follows (docs/rules.md).

When both fail, the result says whether a retry is safe:

- the second may have taken it: uncertain, never retried;
- either was only unavailable: deferred, so the job tries both again later;
- both refused it: the first provider's refusal.
"""

import structlog

from app.infrastructure.sms import (
    SmsDeferred,
    SmsFailure,
    SmsMessage,
    SmsResult,
    SmsTransport,
    SmsUncertain,
)

logger = structlog.get_logger()


class FallbackSms:
    def __init__(self, primary: SmsTransport, secondary: SmsTransport) -> None:
        if primary.name == secondary.name:
            raise ValueError("The fallback SMS provider must differ from the first one")
        self.primary = primary
        self.secondary = secondary
        self.name = f"{primary.name}+{secondary.name}"

    async def send(self, message: SmsMessage) -> SmsResult:
        try:
            return await self.primary.send(message)
        except SmsUncertain:
            raise
        except SmsFailure as first:
            # The first provider did not take the message, so sending it elsewhere is safe.
            logger.warning(
                "sms_fallback",
                provider=self.primary.name,
                fallback=self.secondary.name,
                error_code=first.code,
            )
            try:
                return await self.secondary.send(message)
            except (SmsUncertain, SmsDeferred):
                raise
            except SmsFailure:
                # The second refused it. If the first was only unavailable, its deferral
                # stands and both are tried again; if it refused too, its reason is kept.
                raise first from None
