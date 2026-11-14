"""The identity checks module names the migration module's consumers by kind, as text."""

from app.modules.kyc import service as kyc
from app.modules.migration.jobs import IDENTITY_CHECK_DECIDED, MigrationJobs


def test_each_decision_reaches_a_handler_the_migration_worker_has() -> None:
    kinds = {consumer.kind for consumer in kyc.DECIDED_CONSUMERS}
    assert IDENTITY_CHECK_DECIDED in kinds
    # The handlers are bound methods, so the worker's table is read without a database.
    handled = MigrationJobs.__new__(MigrationJobs).handlers()
    assert IDENTITY_CHECK_DECIDED in handled
