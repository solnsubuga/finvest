"""An email transport for tests that records what it was asked to send."""

from dataclasses import dataclass, field

from app.infrastructure.email import EmailFailure, EmailMessage, SendResult


@dataclass
class RecordingTransport:
    name: str = "recording"
    sent: list[EmailMessage] = field(default_factory=list)
    # Raised, in order, by the next sends. Then sends succeed.
    failures: list[EmailFailure] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> SendResult:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(message)
        return SendResult(provider=self.name, provider_message_id=f"msg-{len(self.sent)}")

    def to(self, address: str) -> list[EmailMessage]:
        return [message for message in self.sent if message.to == address]
