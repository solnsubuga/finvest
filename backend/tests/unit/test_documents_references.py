"""What the client app is told a document is called, and which way an investment note faces."""

from datetime import UTC, datetime

import pytest

from app.modules.documents.service import DocumentKind, DocumentStatus, DocumentView, NoteStage


def view(kind: DocumentKind, key: str = "key") -> DocumentView:
    return DocumentView(
        id="Pq30vNa2AAAAAAAAAAAA",
        client_id="client",
        kind=kind,
        idempotency_key=key,
        period=None,
        source_id=None,
        status=DocumentStatus.READY,
        failure_code=None,
        requested_at=datetime(2026, 11, 14, tzinfo=UTC),
        finished_at=None,
        file_name=None,
        byte_size=None,
    )


@pytest.mark.parametrize(
    ("kind", "prefix"),
    [
        (DocumentKind.MONTHLY_STATEMENT, "ST"),
        (DocumentKind.REQUESTED_STATEMENT, "ST"),
        (DocumentKind.RECEIPT, "RC"),
        (DocumentKind.TAX_CERTIFICATE, "TC"),
        (DocumentKind.INVESTMENT_NOTE, "IN"),
    ],
)
def test_every_kind_has_the_reference_its_pdf_prints(kind: DocumentKind, prefix: str) -> None:
    assert view(kind).reference == f"{prefix}-Pq30vNa2AAAAAAAAAAAA"


def test_only_an_investment_note_says_whether_it_started_or_ended() -> None:
    assert view(DocumentKind.INVESTMENT_NOTE, "started:inv").note is NoteStage.STARTED
    assert view(DocumentKind.INVESTMENT_NOTE, "ended:inv").note is NoteStage.ENDED
    for kind in DocumentKind:
        if kind is not DocumentKind.INVESTMENT_NOTE:
            assert view(kind, "ended:inv").note is None
