"""A booking the worker cannot save says why, in the words of a client's own refusal."""

import pytest

from app.modules.investments.bookings import _failure
from app.shared.errors import ApplicationError, ErrorCode, FieldProblem


def problem(code: ErrorCode, field: str, reason: str) -> ApplicationError:
    return ApplicationError(code, fields=[FieldProblem(field_path=[field], code=reason)])


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (problem("validation_failed", "amount", "product_not_open"), "product_not_open"),
        (problem("validation_failed", "amount", "below_minimum"), "below_minimum"),
        (problem("conflict", "account", "closing"), "closing"),
        (ApplicationError("conflict"), "too_many_unpaid"),
        (ApplicationError("forbidden"), "client_not_able"),
        (ApplicationError("not_found"), "not_found"),
    ],
)
def test_each_refusal_has_its_code(error: ApplicationError, code: str) -> None:
    assert _failure(error) == code
