"""Typed job failures. Their codes and messages are stored and logged, so keep them safe.

Never put personal data, account details, amounts, secrets or provider payloads in a message.
Any other exception is recorded only by its type name.

The three kinds match how a provider call can end:

- deferred (`RetryableJobError`): it did not happen, and trying again is safe.
- rejected (`PermanentJobError`): it did not happen, and trying again cannot succeed.
- uncertain (`UncertainOutcomeError`): it may have happened. Never retried. The job stops for
  reconciliation, which asks the provider what happened before anything else changes.
"""


class JobError(Exception):
    code = "job_failed"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = message[:500] or None
        if code is not None:
            self.code = code[:64]


class RetryableJobError(JobError):
    """A transient failure with no external effect. Retries with bounded backoff."""

    code = "retryable_error"


class PermanentJobError(JobError):
    """Retrying cannot succeed. The job fails at once."""

    code = "permanent_error"


UNCERTAIN_OUTCOME = "uncertain_outcome"


class UncertainOutcomeError(PermanentJobError):
    """An external effect, such as a payout, may or may not have happened.

    The job fails with a code that the worker and operators can never requeue, so nothing risks a
    second external effect. Reconciliation settles it from the provider's own record.
    """

    code = UNCERTAIN_OUTCOME

    def __init__(self, message: str = "") -> None:
        super().__init__(message)


class StaleLeaseError(Exception):
    """The job's fencing token changed: another worker owns it or it already finished."""


class LeadershipLostError(Exception):
    """This scheduler no longer holds the leadership lease it dispatched under."""
