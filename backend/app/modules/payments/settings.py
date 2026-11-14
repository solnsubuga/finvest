from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.shared.money import Currency


class PaymentsSettings(BaseSettings):
    """Which collections provider this process uses, from `FINVEST_PAYMENTS_*`.

    `disabled` is the default: with no provider chosen, no deposit can be started and the
    callback address answers 404. `sandbox` moves no money and must never be set in production.
    `iotec` is ioTec Pay, configured by `FINVEST_IOTEC_*` (`app.infrastructure.iotec`).
    """

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_PAYMENTS_", extra="ignore", hide_input_in_errors=True
    )

    provider: Literal["disabled", "sandbox", "iotec"] = "disabled"
    sandbox_notice_secret: SecretStr | None = None


class BankAccountDetails(BaseModel):
    """One account clients pay into by bank transfer, as the client is shown it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bank_name: str = Field(min_length=1)
    account_name: str = Field(min_length=1)
    account_number: str = Field(min_length=1)
    branch: str | None = None


class BankTransferSettings(BaseSettings):
    """The accounts clients pay into by bank transfer, one per currency.

    From `FINVEST_BANK_TRANSFER_ACCOUNTS`, a JSON object keyed by currency, for example
    `{"UGX": {"bank_name": "...", "account_name": "...", "account_number": "...",
    "branch": null}}`. These are the details printed on an invoice, not a secret, but they are
    set per deployment, so they are not written into the code. A currency with no account
    cannot be paid by transfer: the details route answers `unavailable`. Each account must be
    the bank pot whose statement reconciliation imports, or a transfer can never be matched.
    """

    model_config = SettingsConfigDict(env_prefix="FINVEST_BANK_TRANSFER_", extra="ignore")

    accounts: dict[Currency, BankAccountDetails] = {}


class Deployment(BaseSettings):
    """Where this process is deployed, from `FINVEST_ENVIRONMENT`.

    Every deployed process is given `staging` or `production`. It is not set in local
    development or in tests.
    """

    model_config = SettingsConfigDict(env_prefix="FINVEST_", extra="ignore")

    environment: str | None = None


# The only places the sandbox may answer. Anything else, including a name we do not know,
# is treated as production.
SANDBOX_ENVIRONMENTS = frozenset({None, "local", "staging"})


class SandboxNotAllowed(RuntimeError):
    """The sandbox provider was configured where real money is expected."""
