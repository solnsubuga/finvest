"""Carry identity checks over from Finvest 1.0.

Revision ID: 0052_kyc_carried_over
Revises: 0037_payout_resolutions

A client whose identity Finvest 1.0 had checked, on a document that has not expired, is carried
over at the switch already approved, with a new account number (docs/migration.md). The check
is inserted approved: it was never a draft, was never submitted and has no photos.

* `source` says where a check came from: `finvest2` for one a client handed in, `finvest1` for
  one carried over. `legacy_reference` is the client's Finvest 1.0 reference, set exactly when
  the check was carried over, and used once.
* A carried-over check is approved, has no submission, no provider and no automatic checks.
  Finvest 1.0 did not always record when a document expires, so its expiry may be unknown.
* Only the system on the worker's connection inserts a carried-over check, because the switch
  runs in the worker, and it may insert nothing else. Clients still insert only their own
  drafts and can never set the source. Nobody updates a carried-over check, because a decided
  check never changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0052_kyc_carried_over"
down_revision: str | None = "0037_payout_resolutions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# Adds columns with defaults and loosens one check: the previous release keeps working.
zero_downtime = True

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

COLUMNS = """
ALTER TABLE kyc_checks
    ADD COLUMN source varchar(10) NOT NULL DEFAULT 'finvest2',
    ADD COLUMN legacy_reference varchar(64) COLLATE "C",
    ADD CONSTRAINT ck_kyc_checks_source CHECK (source IN ('finvest2', 'finvest1')),
    ADD CONSTRAINT ck_kyc_checks_legacy_reference CHECK (
        (source = 'finvest1') = (legacy_reference IS NOT NULL)
        AND legacy_reference ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$'),
    ADD CONSTRAINT uq_kyc_checks_legacy_reference UNIQUE (legacy_reference),
    ADD CONSTRAINT ck_kyc_checks_carried_over CHECK (
        source = 'finvest2' OR (
            status = 'approved' AND submission = 0 AND submitted_at IS NULL AND provider IS NULL
            AND provider_reference IS NULL AND automatic_checks = '[]'::jsonb)),
    DROP CONSTRAINT ck_kyc_checks_details,
    -- Nothing leaves draft without confirmed details. A carried-over check was never handed in.
    ADD CONSTRAINT ck_kyc_checks_details CHECK (
        status = 'draft' OR (
            details_confirmed_at IS NOT NULL AND surname IS NOT NULL AND given_names IS NOT NULL
            AND document_number_sealed IS NOT NULL AND date_of_birth_sealed IS NOT NULL
            AND document_fingerprint IS NOT NULL
            AND (source = 'finvest1' OR (
                document_expires_on IS NOT NULL AND submitted_at IS NOT NULL AND submission > 0))))
"""

RULES = [
    "DROP POLICY kyc_checks_app_insert ON kyc_checks",
    f"CREATE POLICY kyc_checks_app_insert ON kyc_checks FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() AND status = 'draft' "
    "AND source = 'finvest2')",
    "DROP POLICY kyc_checks_worker ON kyc_checks",
    f"CREATE POLICY kyc_checks_worker_read ON kyc_checks FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY kyc_checks_worker_update ON kyc_checks FOR UPDATE TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
    f"CREATE POLICY kyc_checks_worker_insert ON kyc_checks FOR INSERT TO {WORKER_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'system' AND source = 'finvest1' AND status = 'approved' "
    "AND decided_by IS NULL)",
    f"GRANT INSERT ON kyc_checks TO {WORKER_ROLE}",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(COLUMNS), *RULES]:
        op.execute(statement)


# Identity records are kept for the regulator; a downgrade never erases them.
CARRIED_OVER_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_checks WHERE source <> 'finvest2') THEN
        RAISE EXCEPTION 'refusing to drop identity checks carried over from Finvest 1.0';
    END IF;
END
$$
"""

PREVIOUS_COLUMNS = """
ALTER TABLE kyc_checks
    DROP CONSTRAINT ck_kyc_checks_details,
    ADD CONSTRAINT ck_kyc_checks_details CHECK (
        status = 'draft' OR (
            details_confirmed_at IS NOT NULL AND surname IS NOT NULL AND given_names IS NOT NULL
            AND document_number_sealed IS NOT NULL AND date_of_birth_sealed IS NOT NULL
            AND document_fingerprint IS NOT NULL AND document_expires_on IS NOT NULL
            AND submitted_at IS NOT NULL AND submission > 0)),
    DROP CONSTRAINT ck_kyc_checks_carried_over,
    DROP CONSTRAINT uq_kyc_checks_legacy_reference,
    DROP CONSTRAINT ck_kyc_checks_legacy_reference,
    DROP CONSTRAINT ck_kyc_checks_source,
    DROP COLUMN legacy_reference,
    DROP COLUMN source
"""

PREVIOUS_RULES = [
    f"REVOKE INSERT ON kyc_checks FROM {WORKER_ROLE}",
    "DROP POLICY kyc_checks_worker_insert ON kyc_checks",
    "DROP POLICY kyc_checks_worker_update ON kyc_checks",
    "DROP POLICY kyc_checks_worker_read ON kyc_checks",
    f"CREATE POLICY kyc_checks_worker ON kyc_checks TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
    "DROP POLICY kyc_checks_app_insert ON kyc_checks",
    f"CREATE POLICY kyc_checks_app_insert ON kyc_checks FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() AND status = 'draft')",
]


def downgrade() -> None:
    op.execute(CARRIED_OVER_GUARD)
    for statement in [*PREVIOUS_RULES, *split(PREVIOUS_COLUMNS)]:
        op.execute(statement)
