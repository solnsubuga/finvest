"""Identity provider adapters. The provider is not chosen yet; see docs/identity-checks.md."""

from app.modules.kyc.domain.provider import Accepted, Completed, Pending, ProviderRequest, Refused


class ManualProvider:
    """No automatic checks: every submission goes to a person. The default until a provider is
    signed, so nobody is ever approved by a check that did not happen."""

    name = "manual"
    automatic = False

    async def submit(self, request: ProviderRequest) -> Accepted | Refused:
        return Refused("no_automatic_checks")

    async def find(self, idempotency_key: str) -> str | None:
        return None

    async def result(self, reference: str) -> Pending | Completed:
        raise LookupError("The manual provider holds no submissions")
