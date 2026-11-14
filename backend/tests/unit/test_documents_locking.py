from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.modules.documents.locking import lock, password_for
from app.modules.documents.pdf import tax_certificate_pdf
from tests.unit.test_documents_rendering import certificate


def test_the_password_is_the_date_of_birth_as_eight_digits() -> None:
    assert password_for(date(1990, 3, 5)) == "05031990"
    assert password_for(date(2001, 12, 31)) == "31122001"


def test_a_locked_pdf_opens_only_with_its_password_and_reads_the_same() -> None:
    content = tax_certificate_pdf(certificate(), reference="TC-1", issued_on=date(2026, 7, 2))
    locked = lock(content, "05031990")
    assert locked != content and locked.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(locked))
    assert reader.is_encrypted
    assert not reader.decrypt("")
    assert not reader.decrypt("19900305")
    assert reader.decrypt("05031990")
    original = PdfReader(BytesIO(content))
    assert [p.extract_text() for p in reader.pages] == [p.extract_text() for p in original.pages]


def test_a_pdf_is_never_locked_with_no_password() -> None:
    with pytest.raises(ValueError):
        lock(b"%PDF-1.4", "")
