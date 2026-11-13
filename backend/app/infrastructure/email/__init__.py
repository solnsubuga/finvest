"""Outgoing email behind one small interface. Resend is the provider."""

from app.infrastructure.email.message import EmailMessage, InvalidEmail, Sender, check_address
from app.infrastructure.email.settings import EmailSettings
from app.infrastructure.email.transports import (
    DisabledTransport,
    EmailDeferred,
    EmailFailure,
    EmailRejected,
    EmailTransport,
    ResendTransport,
    SendResult,
)

__all__ = [
    "DisabledTransport",
    "EmailDeferred",
    "EmailFailure",
    "EmailMessage",
    "EmailRejected",
    "EmailSettings",
    "EmailTransport",
    "InvalidEmail",
    "ResendTransport",
    "SendResult",
    "Sender",
    "check_address",
]
