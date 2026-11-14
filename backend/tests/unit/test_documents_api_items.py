"""What the documents list tells the app each document is about."""

from datetime import UTC, datetime

import pytest

from app.modules.documents.api import _item
from app.modules.documents.service import DocumentKind, DocumentStatus, DocumentView

AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def view(kind: DocumentKind, source_id: str | None) -> DocumentView:
    return DocumentView(
        id="-Odoc000000000000001",
        client_id="-Ocli000000000000001",
        kind=kind,
        idempotency_key="key",
        period=None,
        source_id=source_id,
        status=DocumentStatus.READY,
        failure_code=None,
        requested_at=AT,
        finished_at=AT,
        file_name="file.pdf",
        byte_size=10,
    )


def test_an_investment_note_names_its_investment() -> None:
    item = _item(view(DocumentKind.INVESTMENT_NOTE, "-Oinv000000000000001"))
    assert (item.investment_id, item.deposit_id) == ("-Oinv000000000000001", None)


def test_a_receipt_names_its_payment() -> None:
    item = _item(view(DocumentKind.RECEIPT, "-Odep000000000000001"))
    assert (item.investment_id, item.deposit_id) == (None, "-Odep000000000000001")


@pytest.mark.parametrize(
    "kind",
    [
        DocumentKind.MONTHLY_STATEMENT,
        DocumentKind.REQUESTED_STATEMENT,
        DocumentKind.TAX_CERTIFICATE,
    ],
)
def test_other_documents_name_nothing(kind: DocumentKind) -> None:
    item = _item(view(kind, "-Osrc000000000000001"))
    assert (item.investment_id, item.deposit_id) == (None, None)
