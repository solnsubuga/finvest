"""The committed OpenAPI description is what the apps' generated types are built from."""

from app.api.contract import CONTRACT, render


def test_the_committed_contract_matches_the_routes() -> None:
    committed = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""
    assert committed == render(), (
        "packages/api-client/openapi.json is out of date. Run `make api-contract` and commit it."
    )
