"""Payouts wait until the client is screened clear.

Revision ID: 0181_payout_screening
Revises: 0180_screening

* `screening_payout_clearance(client, at)` answers whether money may leave for a client:
  `clear`, `wait` (a screening case is open, a screen is under way, or a screen moments ago could
  not tell), `screen` (no screen in the last 24 hours) or `blocked` (two members of staff
  confirmed a sanctions match). It is the rule in `screening/domain/standing.py`. A client may
  ask only about themselves; staff and the worker about anyone.
* A payout gains the status `screening`: created, its money reserved, and waiting for a screen.
  Only the worker moves it on, to `queued` or `held`, and only when the client is not blocked
  and nothing is open or under way. Staff may decline it, in their own name, as a held payout.
* A payout is never created `held` or `queued` for a client who is blocked or has an open
  screening case. How recent a screen must be is the application's rule, because with no
  screening provider set up there is no screen to wait for; cases still hold.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0181_payout_screening"
down_revision: str | None = "0180_screening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = "finvest_app"
WORKER = "finvest_worker"
OWN = "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()"
OLD_STATUSES = "'held', 'queued', 'sent', 'uncertain', 'paid', 'failed', 'cancelled'"
STATUSES = f"'screening', {OLD_STATUSES}"

CLEARANCE = """
CREATE FUNCTION public.screening_payout_clearance(p_client_id text, p_at timestamptz)
    RETURNS text
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        last public.screening_runs%ROWTYPE;
    BEGIN
        IF public.finvest_actor_kind() IS NULL
            OR (public.finvest_actor_kind() = 'client'
                AND p_client_id IS DISTINCT FROM public.finvest_actor_id()) THEN
            RAISE EXCEPTION 'a client asks only about themselves' USING ERRCODE = '42501';
        END IF;
        IF EXISTS (
            SELECT FROM public.compliance_cases
             WHERE client_id = p_client_id AND source = 'screening' AND outcome = 'sanctioned') THEN
            RETURN 'blocked';
        END IF;
        IF EXISTS (
            SELECT FROM public.compliance_cases
             WHERE client_id = p_client_id AND source = 'screening' AND status = 'open') THEN
            RETURN 'wait';
        END IF;
        SELECT * INTO last FROM public.screening_runs
         WHERE client_id = p_client_id ORDER BY requested_at DESC, id DESC LIMIT 1;
        IF last.id IS NULL THEN
            RETURN 'screen';
        END IF;
        IF last.status = 'pending' THEN
            RETURN 'wait';
        END IF;
        IF p_at - last.finished_at > interval '24 hours' THEN
            RETURN 'screen';
        END IF;
        IF last.status IN ('unavailable', 'no_subject') THEN
            RETURN 'wait';
        END IF;
        RETURN 'clear';
    END;
    $$
"""

# Every new payout: none starts on its way for a client the screening record holds.
SCREENED = """
CREATE FUNCTION public.finvest_payouts_screened() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.status IN ('held', 'queued')
            AND public.screening_payout_clearance(NEW.client_id, NEW.created_at)
                IN ('blocked', 'wait') THEN
            RAISE EXCEPTION 'a payout for a client being screened waits for the screen'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

MARKER = "        ELSIF actor = 'system' AND NEW.kind <> 'bank' AND step IN ("
# Staff decline a payout waiting for screening as they decline a held one, in their own name.
STAFF_STEPS = "        ELSIF actor = 'staff' AND step IN ('held>queued', 'held>cancelled') THEN"
STAFF_STEPS_NOW = (
    "        ELSIF actor = 'staff' AND step IN ('held>queued', 'held>cancelled', "
    "'screening>cancelled') THEN"
)
RELEASE = """        ELSIF actor = 'system' AND step IN ('screening>queued', 'screening>held') THEN
            -- Released by the worker once the client is screened, and never for a client who
            -- is blocked, has an open case or is being screened.
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'the worker signs nothing in a person''s name'
                    USING ERRCODE = '42501';
            END IF;
            IF public.screening_payout_clearance(NEW.client_id, NEW.updated_at)
                IN ('blocked', 'wait') THEN
                RAISE EXCEPTION 'a payout waits until the client is screened clear'
                    USING ERRCODE = '42501';
            END IF;
"""


def _previous_guard() -> str:
    path = Path(__file__).with_name("0039_payout_bank_batches.py")
    spec = importlib.util.spec_from_file_location("finvest_0039_payout_bank_batches", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.PAYOUTS_GUARD)


def _guard() -> str:
    old = _previous_guard()
    if old.count(MARKER) != 1 or old.count(STAFF_STEPS) != 1:
        raise RuntimeError("The 0039 payouts guard is not the one this revision extends")
    return old.replace(MARKER, RELEASE + MARKER, 1).replace(STAFF_STEPS, STAFF_STEPS_NOW, 1)


def _insert_policies(statuses: str) -> list[str]:
    return [
        "DROP POLICY payouts_app_insert ON payouts",
        "DROP POLICY payouts_worker_insert ON payouts",
        f"CREATE POLICY payouts_app_insert ON payouts FOR INSERT TO {APP} "
        f"WITH CHECK ({OWN} AND status IN ({statuses}) AND provider_reference IS NULL "
        "AND reviewed_by IS NULL)",
        f"CREATE POLICY payouts_worker_insert ON payouts FOR INSERT TO {WORKER} "
        f"WITH CHECK (finvest_actor_kind() = 'system' AND status IN ({statuses}) "
        "AND provider_reference IS NULL AND reviewed_by IS NULL)",
    ]


def _status_rules(statuses: str, open_statuses: str) -> list[str]:
    return [
        "ALTER TABLE payouts DROP CONSTRAINT ck_payouts_status",
        f"ALTER TABLE payouts ADD CONSTRAINT ck_payouts_status CHECK (status IN ({statuses}))",
        "DROP INDEX ix_payouts_open",
        f"CREATE INDEX ix_payouts_open ON payouts (status, updated_at) "
        f"WHERE status IN ({open_statuses})",
    ]


def upgrade() -> None:
    for statement in [
        CLEARANCE,
        "REVOKE ALL ON FUNCTION public.screening_payout_clearance(text, timestamptz) FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION public.screening_payout_clearance(text, timestamptz) "
        f"TO {APP}, {WORKER}",
        SCREENED,
        "REVOKE ALL ON FUNCTION public.finvest_payouts_screened() FROM PUBLIC",
        "CREATE TRIGGER payouts_screened BEFORE INSERT ON payouts "
        "FOR EACH ROW EXECUTE FUNCTION public.finvest_payouts_screened()",
        *_status_rules(STATUSES, "'screening', 'held', 'queued', 'sent', 'uncertain'"),
        *_insert_policies("'screening', 'held', 'queued'"),
        _guard(),
        "CREATE INDEX ix_payouts_screening ON payouts (client_id) WHERE status = 'screening'",
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payouts WHERE status = 'screening') THEN
        RAISE EXCEPTION 'refusing to downgrade while payouts wait for screening';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in [
        "DROP INDEX ix_payouts_screening",
        _previous_guard(),
        *_insert_policies("'held', 'queued'"),
        *_status_rules(OLD_STATUSES, "'held', 'queued', 'sent', 'uncertain'"),
        "DROP TRIGGER payouts_screened ON payouts",
        "DROP FUNCTION public.finvest_payouts_screened()",
        "DROP FUNCTION public.screening_payout_clearance(text, timestamptz)",
    ]:
        op.execute(statement)
