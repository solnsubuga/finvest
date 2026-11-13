"""Follow a goal's savings when they roll over.

Revision ID: 0111_goal_rollovers
Revises: 0041_reconciliation_day_close

A saving that rolls over at maturity becomes a new investment. The worker links that new
investment to the same goal, so the goal's value carries on. What must hold:

* Only the worker records a rollover, as the system, and only from a saving already linked to
  the same goal. A client's own saving never names one.
* The goal must still be active, as for any saving. A closed goal's investments keep running
  but are no longer followed.

The guards also move to `search_path = pg_catalog, pg_temp` with every table named in full.
The savings guard runs as its owner, so the worker can lock the goal without holding UPDATE on
it. See docs/goals.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0111_goal_rollovers"
down_revision: str | None = "0041_reconciliation_day_close"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

GOALS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_goals_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'active' THEN
                RAISE EXCEPTION 'a goal begins active' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'closed' THEN
            RAISE EXCEPTION 'a closed goal never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.client_id <> OLD.client_id OR NEW.currency <> OLD.currency
            OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a goal keeps its client, currency and start' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

SAVINGS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_goal_savings_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a saving towards a goal is never changed or removed'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.rolled_from_investment_id IS NOT NULL
            AND public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the system records a rollover' USING ERRCODE = '42501';
        END IF;
        -- Locks the goal, so a saving and the goal's closing cannot cross.
        IF NOT EXISTS (
            SELECT FROM public.goals WHERE id = NEW.goal_id AND status = 'active' FOR SHARE) THEN
            RAISE EXCEPTION 'savings go only to an active goal' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    "ALTER TABLE goal_savings ADD COLUMN rolled_from_investment_id push_id",
    # The target of the rollover's foreign key, so a rollover names a saving of its own goal.
    "ALTER TABLE goal_savings ADD CONSTRAINT uq_goal_savings_goal UNIQUE (investment_id, goal_id)",
    "ALTER TABLE goal_savings ADD CONSTRAINT fk_goal_savings_rolled_from "
    "FOREIGN KEY (rolled_from_investment_id, goal_id) "
    "REFERENCES goal_savings (investment_id, goal_id)",
    # A saving rolls over once, into one new investment.
    "CREATE UNIQUE INDEX uq_goal_savings_rolled_from ON goal_savings (rolled_from_investment_id) "
    "WHERE rolled_from_investment_id IS NOT NULL",
    GOALS_GUARD,
    SAVINGS_GUARD,
    "REVOKE ALL ON FUNCTION finvest_goal_savings_guard() FROM PUBLIC",
    f"GRANT INSERT ON goal_savings TO {WORKER_ROLE}",
    f"CREATE POLICY goal_savings_worker_insert ON goal_savings FOR INSERT TO {WORKER_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'system' AND rolled_from_investment_id IS NOT NULL)",
    "DROP POLICY goal_savings_app_insert ON goal_savings",
    f"CREATE POLICY goal_savings_app_insert ON goal_savings FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
    "AND rolled_from_investment_id IS NULL)",
]

OLD_GOALS_GUARD = GOALS_GUARD.replace(
    "search_path = pg_catalog, pg_temp", "search_path = pg_catalog, public, pg_temp"
)

OLD_SAVINGS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_goal_savings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a saving towards a goal is never changed or removed'
                USING ERRCODE = '42501';
        END IF;
        -- Locks the goal, so a saving and the goal's closing cannot cross.
        IF NOT EXISTS (
            SELECT FROM goals WHERE id = NEW.goal_id AND status = 'active' FOR SHARE) THEN
            RAISE EXCEPTION 'savings go only to an active goal' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM goal_savings WHERE rolled_from_investment_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the rollovers of goal savings';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # A rollover row is a client's record of where their goal's money went.
    op.execute(EMPTY_GUARD)
    op.execute("DROP POLICY goal_savings_app_insert ON goal_savings")
    op.execute(
        f"CREATE POLICY goal_savings_app_insert ON goal_savings FOR INSERT TO {APP_ROLE} "
        "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())"
    )
    op.execute("DROP POLICY goal_savings_worker_insert ON goal_savings")
    op.execute(f"REVOKE INSERT ON goal_savings FROM {WORKER_ROLE}")
    op.execute(OLD_SAVINGS_GUARD)
    op.execute(OLD_GOALS_GUARD)
    op.execute("DROP INDEX uq_goal_savings_rolled_from")
    op.execute("ALTER TABLE goal_savings DROP CONSTRAINT fk_goal_savings_rolled_from")
    op.execute("ALTER TABLE goal_savings DROP CONSTRAINT uq_goal_savings_goal")
    op.execute("ALTER TABLE goal_savings DROP COLUMN rolled_from_investment_id")
