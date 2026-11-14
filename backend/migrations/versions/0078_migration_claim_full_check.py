"""Send a client whose selfie did not match to the full ID check.

Revision ID: 0078_migration_claim_full_check
Revises: 0077_migration_account_numbers

A claim kept `needs_id_check` as it was set at the switch. Staff deciding that a carried-over
client's selfie and ID photo do not match is final in the identity checks module, and the owner
chose (2026-11-14) that such a client takes the full ID check, as a new client does.

The claim guard now allows `needs_id_check` one change: from false to true, by the system, in
the same update that sends the claim back from identity_submitted to signed_in. Every other rule
of migration 0072 stays as it was.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0078_migration_claim_full_check"
down_revision: str | None = "0077_migration_account_numbers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (actor kind, from, to), as in migration 0072. The steps themselves do not change.
TRANSITIONS = (
    ("staff", "not_invited", "invited"),
    ("staff", "problem_reported", "identity_confirmed"),
    ("client", "invited", "signed_in"),
    ("client", "signed_in", "identity_submitted"),
    ("client", "identity_confirmed", "confirmed"),
    ("client", "identity_confirmed", "problem_reported"),
    ("system", "not_invited", "invited"),
    ("system", "identity_submitted", "identity_confirmed"),
    ("system", "identity_submitted", "signed_in"),
)
_ALLOWED = " OR ".join(
    f"(actor = '{kind}' AND OLD.status = '{before}' AND NEW.status = '{after}')"
    for kind, before, after in TRANSITIONS
)


def guard(*, full_check: bool) -> str:
    # Before this revision the column never changed; after it, only on a failed selfie.
    to_full_check = (
        "NOT (actor = 'system' AND OLD.status = 'identity_submitted' "
        "AND NEW.status = 'signed_in' AND NOT OLD.needs_id_check AND NEW.needs_id_check)"
        if full_check
        else "true"
    )
    return f"""
CREATE OR REPLACE FUNCTION finvest_migration_claim_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'not_invited' OR NEW.reminders_sent <> 0
                OR NEW.problem_text IS NOT NULL THEN
                RAISE EXCEPTION 'a claim starts not invited, with no reminders or problem'
                    USING ERRCODE = '23514', CONSTRAINT = 'migration_claim_step';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'migration claims are never removed' USING ERRCODE = '42501';
        END IF;
        IF NEW.client_id <> OLD.client_id OR NEW.legacy_client_ref <> OLD.legacy_client_ref
            OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a claim keeps who it is for' USING ERRCODE = '42501';
        END IF;
        IF NEW.needs_id_check <> OLD.needs_id_check AND {to_full_check} THEN
            RAISE EXCEPTION 'only a selfie that did not match sends a claim to the full ID check'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.reminders_sent < OLD.reminders_sent THEN
            RAISE EXCEPTION 'reminders are only counted up' USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> OLD.status AND NOT ({_ALLOWED}) THEN
            RAISE EXCEPTION 'a claim cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = '23514', CONSTRAINT = 'migration_claim_step';
        END IF;
        RETURN NEW;
    END;
    $$
"""


def upgrade() -> None:
    op.execute(guard(full_check=True))


def downgrade() -> None:
    op.execute(guard(full_check=False))
