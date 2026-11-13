"""Add goals and the savings made towards them.

Revision ID: 0110_goals
Revises: 0071_migration_totals_confirmed

A goal holds no money. It is a target attached to the client's investments: each saving towards
it is an investment of its own, and `goal_savings` records which goal an investment was made
for. What must hold is enforced here, not only in application code:

* A client creates and changes only their own goals. Staff read every goal and change none.
* A closed goal never changes again, and nothing is saved to it.
* A saving belongs to one goal, of the same client, and once recorded it is never changed or
  removed by any role.
* Nobody deletes a goal.

See docs/goals.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0110_goals"
down_revision: str | None = "0071_migration_totals_confirmed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

# Kept equal to GoalCategory in app/modules/goals/domain/goals.py.
CATEGORIES = (
    "'school_fees', 'emergency', 'land_or_home', 'business', 'wedding', 'retirement', 'other'"
)

TABLES = f"""
CREATE TABLE goals (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    name varchar(60) NOT NULL,
    category varchar(16) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    target_minor bigint NOT NULL,
    -- None for a goal with no date, such as an emergency fund.
    target_date date,
    -- Where its savings are invested. A product code from the investments module.
    product_code varchar(24) COLLATE "C" NOT NULL,
    status varchar(8) NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    closed_at timestamptz,
    CONSTRAINT pk_goals PRIMARY KEY (id),
    -- The target of the foreign key from goal_savings, so a saving names its goal's client.
    CONSTRAINT uq_goals_id_client UNIQUE (id, client_id),
    CONSTRAINT ck_goals_name CHECK (name = btrim(name) AND name <> ''),
    CONSTRAINT ck_goals_category CHECK (category IN ({CATEGORIES})),
    CONSTRAINT ck_goals_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_goals_target CHECK (target_minor > 0),
    CONSTRAINT ck_goals_status CHECK (status IN ('active', 'closed')),
    CONSTRAINT ck_goals_closed CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);
CREATE INDEX ix_goals_client ON goals (client_id, created_at DESC, id DESC);
CREATE INDEX ix_goals_created ON goals (created_at DESC, id DESC);

CREATE TABLE goal_savings (
    -- An investment in the investments module. No foreign key across modules, and an
    -- investment is saved towards one goal at most.
    investment_id push_id NOT NULL,
    goal_id push_id NOT NULL,
    client_id push_id NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_goal_savings PRIMARY KEY (investment_id),
    CONSTRAINT fk_goal_savings_goal FOREIGN KEY (goal_id, client_id)
        REFERENCES goals (id, client_id)
);
CREATE INDEX ix_goal_savings_goal ON goal_savings (goal_id, created_at, investment_id)
"""

GOALS_GUARD = """
CREATE FUNCTION finvest_goals_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
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
CREATE FUNCTION finvest_goal_savings_guard() RETURNS trigger
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

RULES = [
    "REVOKE ALL ON FUNCTION finvest_goals_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_goal_savings_guard() FROM PUBLIC",
    "CREATE TRIGGER goals_guard BEFORE INSERT OR UPDATE ON goals "
    "FOR EACH ROW EXECUTE FUNCTION finvest_goals_guard()",
    "CREATE TRIGGER goal_savings_guard BEFORE INSERT OR UPDATE OR DELETE ON goal_savings "
    "FOR EACH ROW EXECUTE FUNCTION finvest_goal_savings_guard()",
    "REVOKE ALL ON goals, goal_savings FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (name, category, target_minor, target_date, product_code, "
    f"status, updated_at, closed_at) ON goals TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON goal_savings TO {APP_ROLE}",
    f"GRANT SELECT ON goals, goal_savings TO {WORKER_ROLE}",
    "ALTER TABLE goals ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE goal_savings ENABLE ROW LEVEL SECURITY",
    # A client reaches their own goals. Staff read every one; which staff may is a permission
    # checked in the application.
    f"CREATE POLICY goals_app_read ON goals FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY goals_app_insert ON goals FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY goals_app_update ON goals FOR UPDATE TO {APP_ROLE} USING ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY goals_worker ON goals FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY goal_savings_app_read ON goal_savings FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY goal_savings_app_insert ON goal_savings FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY goal_savings_worker ON goal_savings FOR SELECT TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GOALS_GUARD, SAVINGS_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM goals) THEN
        RAISE EXCEPTION 'refusing to drop goals that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Goals are client records; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE goal_savings")
    op.execute("DROP TABLE goals")
    op.execute("DROP FUNCTION finvest_goal_savings_guard()")
    op.execute("DROP FUNCTION finvest_goals_guard()")
