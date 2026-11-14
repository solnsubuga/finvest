"""The statement exports staff may upload, by name. Pure: no I/O.

The standard layout is built in: one row per line, the date as YYYY-MM-DD, and money in and
money out in separate columns. Each provider's own export gets a layout on the console's
Settings screen once one of its files has been seen, set by two people
(`platform_settings`, `statement_layouts`). `csv_format` turns one into the parser's format.
"""

from app.modules.platform_settings.domain.catalogue import StatementLayout
from app.modules.reconciliation.domain.statements import CsvFormat

STANDARD = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)

FORMATS: dict[str, CsvFormat] = {"standard": STANDARD}


def csv_format(layout: StatementLayout) -> CsvFormat:
    """The parser's format for a layout set on Settings. The layout was checked when it was
    proposed and approved, so it names its money columns one way only."""
    if layout.money_in is not None and layout.money_out is not None:
        return CsvFormat(
            value_date=layout.date_column,
            date_format=layout.strptime,
            narrative=layout.narrative,
            reference=layout.reference,
            money_in=layout.money_in,
            money_out=layout.money_out,
        )
    return CsvFormat(
        value_date=layout.date_column,
        date_format=layout.strptime,
        narrative=layout.narrative,
        reference=layout.reference,
        amount=layout.amount,
        direction=layout.direction,
        in_word=layout.in_word or "",
        out_word=layout.out_word or "",
    )
