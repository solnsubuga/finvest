"""Bills the issuer pays at maturity. Runs in the worker, as Finvest itself.

On or after a lot's maturity date the face value it still holds is posted into the trust bank
account, dated on the maturity date, and the maturity is recorded. Migration 0082 checks the
record against the lot and the entry. Reconciliation then expects the money on the bank
statement for that date, and a payment that never came shows up there.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealing import holdings
from app.modules.dealing.domain.settlement import accounts, maturity_entry
from app.modules.ledger import service as ledger
from app.shared.business_day import business_date
from app.shared.clock import Clock

BATCH = 50

# The same two-key lock on a lot as a sale and the database guard, so a maturity and a sale of
# one lot are worked out one after the other.
_LOCK_LOT = text("SELECT pg_advisory_xact_lock(hashtext('dealing_lots'), hashtext(:id))")
_INSERT = text(
    "INSERT INTO dealing_lot_maturities (lot_id, matured_on, face_minor, cost_minor, "
    "ledger_entry_id, recorded_at) VALUES (:lot_id, :matured_on, :face, :cost, :entry_id, :now)"
)


async def record_maturities(session: AsyncSession, clock: Clock, *, limit: int = BATCH) -> int:
    """Record every lot due by today, up to `limit`, in the caller's transaction.

    Returns how many were recorded. Running it again, or twice at once, records each lot once.
    A maturity date in a closed day raises the ledger's `DayClosed`, and nothing is recorded.
    """
    today = business_date(clock.now())
    recorded = 0
    for lot_id in await holdings.due_for_maturity(session, today, limit=limit):
        await session.execute(_LOCK_LOT, {"id": lot_id})
        held, matured = await holdings.left_of_lot(session, lot_id)
        if matured or held is None:
            # Recorded by another run while this one waited, or sold in full before it matured.
            continue
        account_ids = {
            code: (await ledger.open_account(session, spec, clock)).id
            for code, spec in accounts(held.lot.face.currency).items()
        }
        entry = await ledger.post_entry(
            session, maturity_entry(lot_id, held.lot, today, account_ids), clock
        )
        await session.execute(
            _INSERT,
            {
                "lot_id": lot_id,
                "matured_on": held.lot.matures_on,
                "face": held.lot.face.minor,
                "cost": held.lot.cost.minor,
                "entry_id": entry.id,
                "now": clock.now(),
            },
        )
        recorded += 1
    return recorded
