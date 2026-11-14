"""Save towards a goal every month by itself: a goal's saving rule.

Revision ID: 0301_goal_saving_rules
Revises: 0300_system_deposits

A saving rule is a monthly automatic investing plan in the goal's product, set up from the goal.
The plan belongs to the autoinvest module and runs like any other: on its day the worker saves
the investment and prompts the client's phone. `goal_saving_rules` is goals' own record of which
plan saves towards which goal, and each run's investment is linked to the goal as a saving. What
must hold:

* A client sets up a rule only for their own goal, and a goal has at most one rule that has not
  ended. A plan saves towards one goal at most.
* A rule only ever ends, once, by its client. Nothing else about it changes, and nothing is
  deleted.
* Only the worker links a run's investment to the goal, as the system, and only for a rule of
  that same goal and client: a composite foreign key ties the saving to its rule. The goal must
  still be active, as the savings guard of 0111 already requires.
"""

from alembic import op

revision = "0301_goal_saving_rules"
down_revision = "0300_system_deposits"
branch_labels = None
depends_on = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE goal_saving_rules (
    id push_id NOT NULL,
    goal_id push_id NOT NULL,
    client_id push_id NOT NULL,
    -- An automatic investing plan in the autoinvest module. No foreign key across modules.
    plan_id push_id NOT NULL,
    created_at timestamptz NOT NULL,
    ended_at timestamptz,
    CONSTRAINT pk_goal_saving_rules PRIMARY KEY (id),
    CONSTRAINT uq_goal_saving_rules_plan UNIQUE (plan_id),
    -- The target of the foreign key from goal_savings, so a saving names its rule's goal.
    CONSTRAINT uq_goal_saving_rules_id_goal UNIQUE (id, goal_id),
    CONSTRAINT fk_goal_saving_rules_goal FOREIGN KEY (goal_id, client_id)
        REFERENCES goals (id, client_id),
    CONSTRAINT ck_goal_saving_rules_ended CHECK (ended_at IS NULL OR ended_at >= created_at)
)
"""

GUARD = """
CREATE FUNCTION finvest_goal_saving_rules_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'a saving rule is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'client'
            OR public.finvest_actor_id() IS DISTINCT FROM NEW.client_id THEN
            RAISE EXCEPTION 'only its client sets up or ends a saving rule'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.ended_at IS NOT NULL OR NOT EXISTS (
                SELECT FROM public.goals WHERE id = NEW.goal_id AND status = 'active' FOR SHARE)
            THEN
                RAISE EXCEPTION 'a saving rule starts open, for an active goal'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (to_jsonb(NEW) - 'ended_at') <> (to_jsonb(OLD) - 'ended_at')
            OR OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL THEN
            RAISE EXCEPTION 'a saving rule only ends, once' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    TABLE,
    "CREATE UNIQUE INDEX uq_goal_saving_rules_open ON goal_saving_rules (goal_id) "
    "WHERE ended_at IS NULL",
    GUARD,
    "REVOKE ALL ON FUNCTION finvest_goal_saving_rules_guard() FROM PUBLIC",
    "CREATE TRIGGER goal_saving_rules_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON goal_saving_rules FOR EACH ROW EXECUTE FUNCTION finvest_goal_saving_rules_guard()",
    "REVOKE ALL ON goal_saving_rules FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (ended_at) ON goal_saving_rules TO {APP_ROLE}",
    f"GRANT SELECT ON goal_saving_rules TO {WORKER_ROLE}",
    "ALTER TABLE goal_saving_rules ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY goal_saving_rules_app_read ON goal_saving_rules FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY goal_saving_rules_app_insert ON goal_saving_rules FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY goal_saving_rules_app_update ON goal_saving_rules FOR UPDATE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY goal_saving_rules_worker ON goal_saving_rules FOR SELECT TO {WORKER_ROLE} "
    "USING (true)",
    # A run's saving names the rule that made it, of the same goal.
    "ALTER TABLE goal_savings ADD COLUMN rule_id push_id",
    "ALTER TABLE goal_savings ADD CONSTRAINT fk_goal_savings_rule "
    "FOREIGN KEY (rule_id, goal_id) REFERENCES goal_saving_rules (id, goal_id)",
    "ALTER TABLE goal_savings ADD CONSTRAINT ck_goal_savings_one_source "
    "CHECK (num_nonnulls(rule_id, rolled_from_investment_id) <= 1)",
    "DROP POLICY goal_savings_worker_insert ON goal_savings",
    f"CREATE POLICY goal_savings_worker_insert ON goal_savings FOR INSERT TO {WORKER_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'system' "
    "AND (rolled_from_investment_id IS NOT NULL OR rule_id IS NOT NULL))",
    "DROP POLICY goal_savings_app_insert ON goal_savings",
    f"CREATE POLICY goal_savings_app_insert ON goal_savings FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
    "AND rolled_from_investment_id IS NULL AND rule_id IS NULL)",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM goal_saving_rules) THEN
        RAISE EXCEPTION 'refusing to drop the saving rules clients set up';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Rules are client records; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    for statement in (
        "DROP POLICY goal_savings_app_insert ON goal_savings",
        f"CREATE POLICY goal_savings_app_insert ON goal_savings FOR INSERT TO {APP_ROLE} "
        "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id() "
        "AND rolled_from_investment_id IS NULL)",
        "DROP POLICY goal_savings_worker_insert ON goal_savings",
        f"CREATE POLICY goal_savings_worker_insert ON goal_savings FOR INSERT TO {WORKER_ROLE} "
        "WITH CHECK (finvest_actor_kind() = 'system' AND rolled_from_investment_id IS NOT NULL)",
        "ALTER TABLE goal_savings DROP CONSTRAINT ck_goal_savings_one_source",
        "ALTER TABLE goal_savings DROP CONSTRAINT fk_goal_savings_rule",
        "ALTER TABLE goal_savings DROP COLUMN rule_id",
        "DROP TABLE goal_saving_rules",
        "DROP FUNCTION finvest_goal_saving_rules_guard()",
    ):
        op.execute(statement)
