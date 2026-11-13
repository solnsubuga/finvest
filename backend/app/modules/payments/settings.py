from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaymentsSettings(BaseSettings):
    """Which collections provider this process uses, from `FINVEST_PAYMENTS_*`.

    `disabled` is the default: with no provider chosen, no deposit can be started and the
    callback address answers 404. `sandbox` moves no money and must never be set in production.
    The ioTec adapter joins this list when its API details are confirmed.
    """

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_PAYMENTS_", extra="ignore", hide_input_in_errors=True
    )

    provider: Literal["disabled", "sandbox"] = "disabled"
    sandbox_notice_secret: SecretStr | None = None
