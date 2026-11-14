"""The database never lets the API insert a carried-over identity check (migration 0052).

No check is carried over any more: every client takes the full ID check (the owner's decision,
2026-11-14). The table keeps the columns, so its insert policies are still tested.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.shared.actors import SYSTEM
from app.shared.push_ids import push_id
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


INSERT = (
    "INSERT INTO kyc_checks (id, client_id, status, document_type, document_country, surname, "
    "given_names, document_number_sealed, date_of_birth_sealed, document_fingerprint, "
    "details_confirmed_at, decided_at, decided_by, account_number, source, legacy_reference, "
    "created_at, updated_at) VALUES (:id, :client_id, :status, 'national_id', 'UG', 'NAKATO', "
    "'Amara', :sealed, :sealed, :fingerprint, now(), :decided_at, :decided_by, NULL, :source, "
    ":reference, now(), now())"
)


def row(client_id: str, **values: object) -> dict[str, object]:
    return {
        "id": push_id(),
        "client_id": client_id,
        "status": "approved",
        "sealed": b"\x01" * 40,
        "fingerprint": "0" * 64,
        "decided_at": None,
        "decided_by": None,
        "source": "finvest1",
        "reference": f"FV1-{push_id()}",
        **values,
    }


async def test_the_api_connection_can_never_insert_a_carried_over_check(
    app_database: Database,
) -> None:
    client = new_client()
    assert client.id is not None
    staff = new_staff()
    for actor, values in (
        (client, {"status": "draft"}),
        (staff, {"decided_by": staff.id}),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(text(INSERT), row(client.id, **values))


async def test_the_worker_inserts_only_approved_carried_over_checks(
    worker_database: Database,
) -> None:
    client_id = new_client().id or ""
    for values in (
        {"source": "finvest2", "reference": None},
        {"status": "needs_review"},
        {"decided_by": new_staff().id},
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(INSERT), row(client_id, **values))
