"""Index bank receipts by file and by transaction number, to find a receipt used twice.

Revision ID: 0431_payment_receipt_reuse
Revises: 0430_identity_terms_acceptances

Staff checking a bank transfer are told when the same file, or the same bank transaction
number, was handed in on another deposit, by this client or anyone else. That is a sign of
fraud: one real transfer shown to Finvest twice. It changes nothing about crediting, which only
a matched statement line does, and a statement line can confirm one payment only.

The transaction number is compared without case, spaces, dots, slashes or dashes, the way
people retype it. See docs/payments.md#a-receipt-used-before.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0431_payment_receipt_reuse"
down_revision: str | None = "0430_identity_terms_acceptances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_payment_bank_receipts_sha256 ON payment_bank_receipts (sha256)")
    op.execute(
        "CREATE INDEX ix_payment_bank_receipts_transaction_key ON payment_bank_receipts "
        "((upper(regexp_replace(transaction_number, '[ ./-]', '', 'g'))))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_payment_bank_receipts_transaction_key")
    op.execute("DROP INDEX ix_payment_bank_receipts_sha256")
