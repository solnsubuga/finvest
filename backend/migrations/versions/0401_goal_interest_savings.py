"""Record the interest an investment saves to a goal at maturity as a saving towards it.

Revision ID: 0401_goal_interest_savings
Revises: 0400_maturity_interest_to_goal

When an investment that chose `interest_to_goal` matures, its interest starts a new
investment (migration 0400). The worker records that investment in `goal_savings`, naming the
investment whose interest it is. What must hold:

* Only the worker records one, as the system.
* The saving is exactly the investment that interest started: its source is the matured
  investment, of the same client, and the matured investment chose this goal.
* The goal must still be active, as for any saving. A goal closed in between takes none, and
  the investment carries on as the client's own, as a closed goal's savings do.
* One saving for each maturity's interest, and a saving has one source at most.

See docs/goals.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0401_goal_interest_savings"
down_revision: str | None = "0400_maturity_interest_to_goal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

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
        -- The investment a matured investment's interest started, for the goal it chose.
        IF NEW.interest_from_investment_id IS NOT NULL AND (
            public.finvest_actor_kind() IS DISTINCT FROM 'system'
            OR NOT EXISTS (
                SELECT FROM public.investments i
                JOIN public.investments f ON f.id = i.source_id
                WHERE i.id = NEW.investment_id AND i.source_type = 'goal_interest'
                    AND i.source_id = NEW.interest_from_investment_id
                    AND i.client_id = NEW.client_id AND f.client_id = NEW.client_id
                    AND f.interest_goal_id = NEW.goal_id)) THEN
            RAISE EXCEPTION 'only the system records interest saved to the goal it was chosen for'
                USING ERRCODE = '42501';
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

OLD_SAVINGS_GUARD = """
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
    "ALTER TABLE goal_savings ADD COLUMN interest_from_investment_id push_id",
    "ALTER TABLE goal_savings DROP CONSTRAINT ck_goal_savings_one_source",
    "ALTER TABLE goal_savings ADD CONSTRAINT ck_goal_savings_one_source "
    "CHECK (num_nonnulls(rule_id, rolled_from_investment_id, interest_from_investment_id) <= 1)",
    # A maturity's interest is saved once.
    "CREATE UNIQUE INDEX uq_goal_savings_interest_from ON goal_savings "
    "(interest_from_investment_id) WHERE interest_from_investment_id IS NOT NULL",
    SAVINGS_GUARD,
    "REVOKE ALL ON FUNCTION finvest_goal_savings_guard() FROM PUBLIC",
    "DROP POLICY goal_savings_worker_insert ON goal_savings",
    f"CREATE POLICY goal_savings_worker_insert ON goal_savings FOR INSERT TO {WORKER_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'system' AND (rolled_from_investment_id IS NOT NULL "
    "OR rule_id IS NOT NULL OR interest_from_investment_id IS NOT NULL))",
    "DROP POLICY goal_savings_app_insert ON goal_savings",
    f"CREATE POLICY goal_savings_app_insert ON goal_savings FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
    "AND rolled_from_investment_id IS NULL AND rule_id IS NULL "
    "AND interest_from_investment_id IS NULL)",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM goal_savings WHERE interest_from_investment_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the interest clients saved to their goals';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Savings are client records; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    for statement in (
        "DROP POLICY goal_savings_app_insert ON goal_savings",
        f"CREATE POLICY goal_savings_app_insert ON goal_savings FOR INSERT TO {APP_ROLE} "
        "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
        "AND rolled_from_investment_id IS NULL AND rule_id IS NULL)",
        "DROP POLICY goal_savings_worker_insert ON goal_savings",
        f"CREATE POLICY goal_savings_worker_insert ON goal_savings FOR INSERT TO {WORKER_ROLE} "
        "WITH CHECK (finvest_actor_kind() = 'system' "
        "AND (rolled_from_investment_id IS NOT NULL OR rule_id IS NOT NULL))",
        OLD_SAVINGS_GUARD,
        "DROP INDEX uq_goal_savings_interest_from",
        "ALTER TABLE goal_savings DROP CONSTRAINT ck_goal_savings_one_source",
        "ALTER TABLE goal_savings ADD CONSTRAINT ck_goal_savings_one_source "
        "CHECK (num_nonnulls(rule_id, rolled_from_investment_id) <= 1)",
        "ALTER TABLE goal_savings DROP COLUMN interest_from_investment_id",
    ):
        op.execute(statement)
