"""The statement exports staff may upload, by name. Pure: no I/O.

Each provider's export gets a format once one of its files has been seen. Until then, staff
upload the standard layout: one row per line, the date as YYYY-MM-DD, and money in and money out
in separate columns.
"""

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
