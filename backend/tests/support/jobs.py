"""Running a test's jobs on the shared test database."""

from sqlalchemy import text

from app.infrastructure.jobs import Worker
from app.shared.actors import SYSTEM

_DUE = text(
    "SELECT count(*) FROM jobs WHERE queue = :queue AND status = 'pending' "
    "AND kind = ANY(CAST(:kinds AS text[])) AND run_at <= :now"
)


async def run_due(worker: Worker, *, claims: int = 5_000) -> None:
    """Run every job of the worker's kinds that is due now, including ones they queue for now.

    The database is shared, so earlier tests may have left jobs of these kinds. A claim that
    finds only expired ones fails them and returns nothing, so an empty claim does not mean
    nothing is due: this stops only once no job of these kinds is due.
    """
    for _ in range(claims):
        if await worker.run_once():
            continue
        async with worker.database.actor_transaction(SYSTEM) as session:
            due = await session.scalar(
                _DUE,
                {
                    "queue": worker.config.queue,
                    "kinds": sorted(worker.handlers),
                    "now": worker.clock.now(),
                },
            )
        if not due:
            return
    raise AssertionError(f"jobs were still due after {claims} claims")
