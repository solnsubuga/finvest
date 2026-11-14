"""Identity provider adapters. The provider is not chosen yet; see docs/identity-checks.md."""

from app.modules.kyc.domain.face_confirmations import (
    MatchAnswer,
    MatchNotRun,
    MatchPending,
    MatchRefused,
    MatchSent,
)
from app.modules.kyc.domain.liveness import Live, NotLive, NotStarted, Started, StillOpen
from app.modules.kyc.domain.provider import (
    Accepted,
    AutomaticCheck,
    CheckOutcome,
    Completed,
    Pending,
    PhotoRef,
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


# The smallest JPEG header the photo check accepts. Not a picture: the stub captures nothing.
STUB_CAPTURE = b"\xff\xd8\xff\xe0 development stub capture"


class DevelopmentStubLiveness:
    """Pretends to run a liveness check, for local development only. It checks nothing.

    The page it hands out is a placeholder, and the first read says a live person was seen,
    with a stand-in capture. Paired only with `DevelopmentStubProvider`, so it is refused in
    production and never approves a client.
    """

    name = "development-stub"

    async def start(self, key: str) -> Started | NotStarted:
        return Started(request_id=key, url=f"https://liveness.invalid/{key}")

    async def result(self, request_id: str) -> StillOpen | Live | NotLive:
        return Live(photo=STUB_CAPTURE, content_type="image/jpeg")


class DevelopmentStubFaceMatcher:
    """Pretends to compare two faces, for local development only. It checks nothing.

    Every comparison matches, so a face confirmation can be tried end to end. Paired only with
    `DevelopmentStubLiveness`, so it is refused in production and never confirms a real change.
    """

    name = "development-stub"

    async def send(self, key: str, live: PhotoRef, on_file: PhotoRef) -> MatchSent | MatchRefused:
        return MatchSent(request_id=key)

    async def result(self, request_id: str) -> MatchPending | MatchAnswer | MatchNotRun:
        return MatchAnswer(score=97, identical=True)
