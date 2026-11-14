"""What the tax module offers the rest of the backend. Other modules import from here.

`expected_bank_movements` is for reconciliation, which pairs each approved payment to URA with
the trust bank account's statement line by the bank's reference.

`certificate_figures` is for the documents module, which draws and sends the yearly
certificate. It is the one place a client is shown a tax amount. No figure here is a rate.
"""

from app.modules.tax.domain.periods import TaxYear, parse_tax_year, tax_year_of
from app.modules.tax.domain.withholding import (
    Certificate,
    CertificateLine,
    CertificateTotal,
    DoesNotAddUp,
)
from app.modules.tax.remittances import (
    BankPayment,
    RemittanceView,
    expected_bank_movements,
    held_for_ura,
    waiting,
)
from app.modules.tax.withholding import NoAccount, certificate_figures

__all__ = [
    "BankPayment",
    "Certificate",
    "CertificateLine",
    "CertificateTotal",
    "DoesNotAddUp",
    "NoAccount",
    "RemittanceView",
    "TaxYear",
    "certificate_figures",
    "expected_bank_movements",
    "held_for_ura",
    "parse_tax_year",
    "tax_year_of",
    "waiting",
]
