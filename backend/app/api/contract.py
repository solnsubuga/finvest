"""Write the API's OpenAPI description to packages/api-client, where the apps' types come from.

uv run python -m app.api.contract

The description depends only on the routes and models, so this builds the application with
placeholder settings and never connects to a database. A unit test fails when the committed
file no longer matches, so a change to a route or a model lands with its new contract.
"""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.api.app import create_app
from app.infrastructure.settings import Settings

CONTRACT = Path(__file__).resolve().parents[3] / "packages" / "api-client" / "openapi.json"


def contract() -> dict[str, Any]:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://contract@localhost/contract"),
        api_docs=True,
    )
    return create_app(settings).openapi()


def render() -> str:
    return json.dumps(contract(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    CONTRACT.write_text(render(), encoding="utf-8")
    sys.stdout.write(f"Wrote {CONTRACT}\n")


if __name__ == "__main__":
    main()
