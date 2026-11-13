"""Add group goals: one target that several clients save towards, each with their own goal.

Revision ID: 0112_goal_groups
Revises: 0082_dealing_maturities

A group goal holds no money, and neither does a member. Each member saves into their own goal,
whose savings are their own investments. The group only adds the shared target and how far the
members are together. Nothing is pooled and nothing moves between members. What must hold is
enforced here, not only in application code:

* Only the person who set a group up changes it, invites to it, removes members or closes it.
  A closed group never changes again, and nobody joins it.
* A member joins with their own active goal in the group's currency, either as the one who set
  the group up or with the group's current invite. A group has at most ten members at once, and
  the check locks the group, so two joining at once cannot pass it.
* Members see the group and its members. Nobody else does, except staff, who change nothing.
* A member row keeps its group, goal, client and joining time. Only the member changes their
  name in the group. The member leaves, or the one who set the group up removes them, once and
  for good. That person never leaves their own active group: they close it instead.
* What each member's goal is worth, as the group sees it, is written by the worker only, as the
  system. A client never writes a figure the group is shown.
* Nobody deletes a group or a member row.

See docs/goals.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0112_goal_groups"
down_revision: str | None = "0082_dealing_maturities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

# Kept equal to GoalCategory in app/modules/goals/domain/goals.py.
CATEGORIES = (
    "'school_fees', 'emergency', 'land_or_home', 'business', 'wedding', 'retirement', 'other'"
)
# Kept equal to MAX_MEMBERS in app/modules/goals/domain/groups.py.
MAX_MEMBERS = 10

TABLES = f"""
CREATE TABLE goal_groups (
    id push_id NOT NULL,
    -- The client who set the group up, in the identity module. No foreign key across modules.
    owner_client_id push_id NOT NULL,
    name varchar(60) NOT NULL,
    category varchar(16) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    target_minor bigint NOT NULL,
    target_date date,
    status varchar(8) NOT NULL DEFAULT 'active',
    -- SHA-256 of the current invite code. The code itself is shown once and never stored.
    invite_hash bytea,
    invite_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    closed_at timestamptz,
    CONSTRAINT pk_goal_groups PRIMARY KEY (id),
    CONSTRAINT ck_goal_groups_name CHECK (name = btrim(name) AND name <> ''),
    CONSTRAINT ck_goal_groups_category CHECK (category IN ({CATEGORIES})),
    CONSTRAINT ck_goal_groups_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_goal_groups_target CHECK (target_minor > 0),
    CONSTRAINT ck_goal_groups_status CHECK (status IN ('active', 'closed')),
    CONSTRAINT ck_goal_groups_closed CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
    CONSTRAINT ck_goal_groups_invite CHECK (
        (invite_hash IS NULL) = (invite_expires_at IS NULL)
        AND (invite_hash IS NULL OR octet_length(invite_hash) = 32))
);
CREATE UNIQUE INDEX uq_goal_groups_invite ON goal_groups (invite_hash)
    WHERE invite_hash IS NOT NULL;
CREATE INDEX ix_goal_groups_created ON goal_groups (created_at DESC, id DESC);

CREATE TABLE goal_group_members (
    -- The member's own goal. A goal is in one group at most, once.
    goal_id push_id NOT NULL,
    group_id push_id NOT NULL,
    client_id push_id NOT NULL,
    -- How the other members see this one, as they wrote it.
    display_name varchar(30) NOT NULL,
    -- The invite the member joined with. None for the one who set the group up.
    joined_with bytea,
    joined_at timestamptz NOT NULL,
    left_at timestamptz,
    -- What the goal's running investments were worth after tax on `value_on`, written by the
    -- worker. Zero until its first refresh.
    value_minor bigint NOT NULL DEFAULT 0,
    value_on date,
    CONSTRAINT pk_goal_group_members PRIMARY KEY (goal_id),
    CONSTRAINT fk_goal_group_members_group FOREIGN KEY (group_id) REFERENCES goal_groups (id),
    CONSTRAINT fk_goal_group_members_goal FOREIGN KEY (goal_id, client_id)
        REFERENCES goals (id, client_id),
    CONSTRAINT ck_goal_group_members_name CHECK (
        display_name = btrim(display_name) AND display_name <> ''),
    CONSTRAINT ck_goal_group_members_value CHECK (value_minor >= 0)
);
-- A client is in a group once at a time.
CREATE UNIQUE INDEX uq_goal_group_members_client ON goal_group_members (group_id, client_id)
    WHERE left_at IS NULL;
CREATE INDEX ix_goal_group_members_group ON goal_group_members (group_id, joined_at, goal_id);
CREATE INDEX ix_goal_group_members_client ON goal_group_members (client_id)
    WHERE left_at IS NULL
"""

# Whether the signed-in client is a current member of the group. Runs as its owner, so a row
# policy can ask it without the member table's own policy asking it again.
MEMBER_OF = """
CREATE FUNCTION finvest_goal_group_member(group_id text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT public.finvest_actor_kind() = 'client' AND EXISTS (
            SELECT FROM public.goal_group_members m
            WHERE m.group_id = $1 AND m.client_id = public.finvest_actor_id()
                AND m.left_at IS NULL)
    $$
"""

OWNER_OF = """
CREATE FUNCTION finvest_goal_group_owner(group_id text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT public.finvest_actor_kind() = 'client' AND EXISTS (
            SELECT FROM public.goal_groups g
            WHERE g.id = $1 AND g.owner_client_id = public.finvest_actor_id())
    $$
"""

# Whether the signed-in client may join the group: as the one who set it up, with no invite, or
# with the group's current invite. Whether the invite is still in date is checked by the
# application against its clock, because the database's own clock is not the one injected.
ADMITS = """
CREATE FUNCTION finvest_goal_group_admits(group_id text, joined_with bytea) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT public.finvest_actor_kind() = 'client' AND EXISTS (
            SELECT FROM public.goal_groups g
            WHERE g.id = $1 AND (
                ($2 IS NULL AND g.owner_client_id = public.finvest_actor_id())
                OR ($2 IS NOT NULL AND g.invite_hash = $2)))
    $$
"""

# What someone holding an invite sees before they join: the group, how many are in it and how
# the one who set it up is named. Nothing about any member's savings.
INVITE = """
CREATE FUNCTION finvest_goal_group_invite(invite_hash bytea)
    RETURNS TABLE (
        id text, name text, category text, currency text, target_minor bigint,
        target_date date, status text, invite_expires_at timestamptz, owner_name text,
        members bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT g.id, g.name, g.category, g.currency, g.target_minor, g.target_date, g.status,
            g.invite_expires_at,
            (SELECT o.display_name FROM public.goal_group_members o
                WHERE o.group_id = g.id AND o.client_id = g.owner_client_id
                    AND o.left_at IS NULL),
            (SELECT count(*) FROM public.goal_group_members c
                WHERE c.group_id = g.id AND c.left_at IS NULL)
        FROM public.goal_groups g
        WHERE public.finvest_actor_kind() = 'client' AND g.invite_hash = $1
    $$
"""

GROUPS_GUARD = """
CREATE FUNCTION finvest_goal_groups_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'a group goal is never removed' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'active' THEN
                RAISE EXCEPTION 'a group goal begins active' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'closed' THEN
            RAISE EXCEPTION 'a closed group goal never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.owner_client_id <> OLD.owner_client_id OR NEW.currency <> OLD.currency
            OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a group goal keeps its owner, currency and start'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'closed' AND NEW.invite_hash IS NOT NULL THEN
            RAISE EXCEPTION 'a closed group goal has no invite' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

MEMBERS_GUARD = """
CREATE FUNCTION finvest_goal_group_members_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        grp record;
        actor_kind text := public.finvest_actor_kind();
        actor_id text := public.finvest_actor_id();
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'a member of a group goal is never removed' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.left_at IS NOT NULL OR NEW.value_minor <> 0 OR NEW.value_on IS NOT NULL THEN
                RAISE EXCEPTION 'a member joins with nothing recorded yet'
                    USING ERRCODE = '42501';
            END IF;
            -- Locks the group, so members joining at the same moment are counted one by one,
            -- and a join and the group's closing cannot cross.
            SELECT g.status, g.currency, g.owner_client_id INTO grp
                FROM public.goal_groups g WHERE g.id = NEW.group_id FOR UPDATE;
            IF NOT FOUND OR grp.status <> 'active' THEN
                RAISE EXCEPTION 'members join only an active group goal'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (
                SELECT FROM public.goals o WHERE o.id = NEW.goal_id AND o.status = 'active'
                    AND o.currency = grp.currency) THEN
                RAISE EXCEPTION 'a member joins with an active goal in the group currency'
                    USING ERRCODE = '42501';
            END IF;
            IF (SELECT count(*) FROM public.goal_group_members m
                    WHERE m.group_id = NEW.group_id AND m.left_at IS NULL) >= __MAX_MEMBERS__ THEN
                RAISE EXCEPTION 'a group goal is full' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.left_at IS NOT NULL THEN
            RAISE EXCEPTION 'a member who left never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.group_id <> OLD.group_id OR NEW.client_id <> OLD.client_id
            OR NEW.joined_at <> OLD.joined_at
            OR NEW.joined_with IS DISTINCT FROM OLD.joined_with THEN
            RAISE EXCEPTION 'a member keeps their group, goal and joining'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.value_minor <> OLD.value_minor OR NEW.value_on IS DISTINCT FROM OLD.value_on)
            AND actor_kind IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the system records what a member has saved'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.display_name <> OLD.display_name AND (
            actor_kind IS DISTINCT FROM 'client' OR actor_id IS DISTINCT FROM OLD.client_id) THEN
            RAISE EXCEPTION 'only the member changes their name in the group'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.left_at IS NOT NULL THEN
            SELECT g.owner_client_id, g.status INTO grp
                FROM public.goal_groups g WHERE g.id = OLD.group_id;
            IF OLD.client_id = grp.owner_client_id AND grp.status = 'active' THEN
                RAISE EXCEPTION 'the one who set a group goal up closes it instead of leaving'
                    USING ERRCODE = '42501';
            END IF;
            IF actor_kind IS DISTINCT FROM 'client' OR (
                actor_id IS DISTINCT FROM OLD.client_id
                AND actor_id IS DISTINCT FROM grp.owner_client_id) THEN
                RAISE EXCEPTION 'a member leaves, or is removed by the one who set the group up'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
""".replace("__MAX_MEMBERS__", str(MAX_MEMBERS))

RULES = [
    "REVOKE ALL ON FUNCTION finvest_goal_group_member(text) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_goal_group_owner(text) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_goal_group_admits(text, bytea) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_goal_group_invite(bytea) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_goal_groups_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_goal_group_members_guard() FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION finvest_goal_group_member(text) TO {APP_ROLE}",
    f"GRANT EXECUTE ON FUNCTION finvest_goal_group_owner(text) TO {APP_ROLE}",
    f"GRANT EXECUTE ON FUNCTION finvest_goal_group_admits(text, bytea) TO {APP_ROLE}",
    f"GRANT EXECUTE ON FUNCTION finvest_goal_group_invite(bytea) TO {APP_ROLE}",
    "CREATE TRIGGER goal_groups_guard BEFORE INSERT OR UPDATE OR DELETE ON goal_groups "
    "FOR EACH ROW EXECUTE FUNCTION finvest_goal_groups_guard()",
    "CREATE TRIGGER goal_group_members_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON goal_group_members FOR EACH ROW EXECUTE FUNCTION finvest_goal_group_members_guard()",
    "REVOKE ALL ON goal_groups, goal_group_members FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (name, category, target_minor, target_date, status, "
    f"invite_hash, invite_expires_at, updated_at, closed_at) ON goal_groups TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (display_name, left_at) ON goal_group_members TO {APP_ROLE}",
    f"GRANT SELECT ON goal_groups TO {WORKER_ROLE}",
    f"GRANT SELECT, UPDATE (value_minor, value_on) ON goal_group_members TO {WORKER_ROLE}",
    "ALTER TABLE goal_groups ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE goal_group_members ENABLE ROW LEVEL SECURITY",
    # Members, and the one who set the group up, reach the group. Staff read every one; which
    # staff may is a permission checked in the application.
    f"CREATE POLICY goal_groups_app_read ON goal_groups FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND (owner_client_id = finvest_actor_id() "
    "OR finvest_goal_group_member(id))) OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY goal_groups_app_insert ON goal_groups FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "finvest_actor_kind() = 'client' AND owner_client_id = finvest_actor_id())",
    f"CREATE POLICY goal_groups_app_update ON goal_groups FOR UPDATE TO {APP_ROLE} USING ("
    "finvest_actor_kind() = 'client' AND owner_client_id = finvest_actor_id())",
    f"CREATE POLICY goal_groups_worker ON goal_groups FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY goal_group_members_app_read ON goal_group_members FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND (client_id = finvest_actor_id() "
    "OR finvest_goal_group_member(group_id))) OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY goal_group_members_app_insert ON goal_group_members FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'client' "
    "AND client_id = finvest_actor_id() AND finvest_goal_group_admits(group_id, joined_with))",
    f"CREATE POLICY goal_group_members_app_update ON goal_group_members FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'client' "
    "AND (client_id = finvest_actor_id() OR finvest_goal_group_owner(group_id)))",
    f"CREATE POLICY goal_group_members_worker_read ON goal_group_members FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY goal_group_members_worker_update ON goal_group_members FOR UPDATE "
    f"TO {WORKER_ROLE} USING (finvest_actor_kind() = 'system')",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [
        *split(TABLES),
        MEMBER_OF,
        OWNER_OF,
        ADMITS,
        INVITE,
        GROUPS_GUARD,
        MEMBERS_GUARD,
        *RULES,
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM goal_groups) THEN
        RAISE EXCEPTION 'refusing to drop group goals that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Group goals are client records; a downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE goal_group_members")
    op.execute("DROP TABLE goal_groups")
    op.execute("DROP FUNCTION finvest_goal_group_members_guard()")
    op.execute("DROP FUNCTION finvest_goal_groups_guard()")
    op.execute("DROP FUNCTION finvest_goal_group_invite(bytea)")
    op.execute("DROP FUNCTION finvest_goal_group_admits(text, bytea)")
    op.execute("DROP FUNCTION finvest_goal_group_owner(text)")
    op.execute("DROP FUNCTION finvest_goal_group_member(text)")
