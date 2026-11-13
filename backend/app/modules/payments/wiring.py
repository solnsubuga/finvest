"""Choose the collections provider for this process from its settings."""

from app.modules.payments.domain.provider import CollectionsProvider
from app.modules.payments.providers.sandbox import SandboxCollections
from app.modules.payments.settings import PaymentsSettings
from app.shared.clock import Clock


def collections_provider(settings: PaymentsSettings, clock: Clock) -> CollectionsProvider | None:
    """The configured provider, or None when payments are disabled in this process."""
    if settings.provider == "sandbox":
        secret = settings.sandbox_notice_secret
        return SandboxCollections(
            clock, notice_secret=secret.get_secret_value() if secret else None
        )
    return None
