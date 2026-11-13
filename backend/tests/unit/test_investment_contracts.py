"""Clients and the public never see tax, a rate before tax, or interest before tax."""

import pytest
from pydantic import BaseModel

from app.modules.investments import api

PUBLIC_AND_CLIENT = [api.ProductOut, api.PublicProducts, api.QuoteOut, api.ClientInvestmentOut]
FORBIDDEN = ("tax", "gross", "bps")


def field_names(model: type[BaseModel]) -> list[str]:
    names: list[str] = []
    for name, info in model.model_fields.items():
        names.append(name)
        nested = info.annotation
        for candidate in getattr(nested, "__args__", (nested,)):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                names.extend(field_names(candidate))
    return names


@pytest.mark.parametrize("model", PUBLIC_AND_CLIENT, ids=lambda model: model.__name__)
def test_no_client_or_public_contract_carries_a_tax_figure(model: type[BaseModel]) -> None:
    names = field_names(model)
    assert names
    assert not [name for name in names if any(word in name for word in FORBIDDEN)]


def test_the_staff_contracts_do_carry_the_tax() -> None:
    assert "tax_bps" in field_names(api.StaffInvestmentOut)
    assert "tax_minor" in field_names(api.InterestRunOut)
