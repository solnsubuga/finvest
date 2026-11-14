"""Choose how this process reads the ioTec wallet, from the payments settings."""

from app.infrastructure.iotec import IotecSettings, IotecTokens
from app.modules.payments.settings import PaymentsSettings
from app.modules.reconciliation.domain.wallet import WalletBalances
from app.modules.reconciliation.providers.iotec import IotecWallet
from app.shared.clock import Clock


def wallet_balances(settings: PaymentsSettings, clock: Clock) -> WalletBalances | None:
    """The ioTec wallet reader, or None where ioTec is not the provider."""
    if settings.provider != "iotec":
        return None
    iotec = IotecSettings()
    http = iotec.http_client()
    return IotecWallet(iotec, IotecTokens(iotec, http, clock), http, clock)
