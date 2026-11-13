"""Identity provider adapters. The provider is not chosen yet; see docs/identity-checks.md."""

from app.modules.kyc.domain.provider import (
    Accepted,
    AutomaticCheck,
    CheckOutcome,
    Completed,
    Pending,
    ProviderRequest,
    Refused,
    Verdict,
)


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


class DevelopmentStubProvider:
    """Pretends to be a provider, for local development only. It checks nothing.

    Every submission "passes", except a document number ending in 0, whose selfie comes back
    unsure, so both paths of the review screen can be tried. Its answers still go to a person
    while automatic approval is off, and `KycSettings` refuses to pair it with automatic
    approval, so the stub can never approve a client.
    """

    name = "development-stub"
    automatic = True

    async def submit(self, request: ProviderRequest) -> Accepted | Refused:
        unsure = request.details.document_number.endswith("0")
        return Accepted(f"stub:{'unsure' if unsure else 'pass'}:{request.idempotency_key}")

    async def find(self, idempotency_key: str) -> str | None:
        # It keeps nothing, so it never claims to hold a submission.
        return None

    async def result(self, reference: str) -> Pending | Completed:
        selfie = (
            CheckOutcome(AutomaticCheck.SELFIE_MATCH, Verdict.UNSURE, 62)
            if reference.startswith("stub:unsure:")
            else CheckOutcome(AutomaticCheck.SELFIE_MATCH, Verdict.PASS, 96)
        )
        return Completed(
            (
                CheckOutcome(AutomaticCheck.DOCUMENT_GENUINE, Verdict.PASS),
                selfie,
                CheckOutcome(AutomaticCheck.SCREENING, Verdict.PASS),
            )
        )
