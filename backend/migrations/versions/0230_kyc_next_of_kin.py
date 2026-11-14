"""Add a client's next of kin.

Revision ID: 0230_kyc_next_of_kin
Revises: 0076_migration_carry_resolutions

* `kyc_next_of_kin_lists` has one row per client who has saved a list, even an empty one. A
  change locks it first, so two saves from two devices cannot interleave.
* `kyc_next_of_kin` holds the people. Name, mobile number and ID number are sealed
  (app/infrastructure/sealing.py): the database and its backups hold no readable copy. The
  relationship and the share stay readable.
* Shares are whole basis points. A constraint trigger checks at commit that a client's shares
  add up to exactly 10,000, or that the list is empty, and that nobody is named twice in a place.
* A client reads and changes only their own list. Staff read every list and change none. The
  worker sees nothing. A change replaces the whole list, so the app deletes and inserts people
  and never updates one.

See docs/client-profile.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0230_kyc_next_of_kin"
down_revision: str | None = "0076_migration_carry_resolutions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"

# Version byte, key id, nonce and tag are 30 bytes; a name is at most 100 characters of UTF-8.
SEALED = "octet_length({column}) BETWEEN 30 AND 512"

TABLES = f"""
CREATE TABLE kyc_next_of_kin_lists (
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    people smallint NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_kyc_next_of_kin_lists PRIMARY KEY (client_id),
    CONSTRAINT ck_kyc_next_of_kin_lists_people CHECK (people BETWEEN 0 AND 5)
);
CREATE TABLE kyc_next_of_kin (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    position smallint NOT NULL,
    relationship text NOT NULL,
    share_bps integer NOT NULL,
    full_name_sealed bytea NOT NULL,
    phone_sealed bytea NOT NULL,
    id_number_sealed bytea NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_kyc_next_of_kin PRIMARY KEY (id),
    CONSTRAINT fk_kyc_next_of_kin_client_id_kyc_next_of_kin_lists
        FOREIGN KEY (client_id) REFERENCES kyc_next_of_kin_lists (client_id),
    CONSTRAINT uq_kyc_next_of_kin_position UNIQUE (client_id, position),
    CONSTRAINT ck_kyc_next_of_kin_position CHECK (position BETWEEN 0 AND 4),
    CONSTRAINT ck_kyc_next_of_kin_relationship CHECK (relationship IN (
        'spouse', 'child', 'parent', 'sibling', 'other_relative', 'friend', 'other')),
    CONSTRAINT ck_kyc_next_of_kin_share CHECK (share_bps BETWEEN 1 AND 10000),
    CONSTRAINT ck_kyc_next_of_kin_full_name_sealed
        CHECK ({SEALED.format(column="full_name_sealed")}),
    CONSTRAINT ck_kyc_next_of_kin_phone_sealed CHECK ({SEALED.format(column="phone_sealed")}),
    CONSTRAINT ck_kyc_next_of_kin_id_number_sealed
        CHECK ({SEALED.format(column="id_number_sealed")})
)
"""

# Runs at commit, once the whole list is in place, so a save may delete and insert in any order.
SHARES = """
CREATE FUNCTION finvest_kyc_next_of_kin_shares() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        v_client push_id := COALESCE(NEW.client_id, OLD.client_id);
        v_people integer;
        v_total bigint;
        v_listed smallint;
    BEGIN
        SELECT count(*), COALESCE(sum(share_bps), 0) INTO v_people, v_total
          FROM kyc_next_of_kin WHERE client_id = v_client;
        SELECT people INTO v_listed FROM kyc_next_of_kin_lists WHERE client_id = v_client;
        IF v_people > 0 AND v_total <> 10000 THEN
            RAISE EXCEPTION 'next of kin shares must add up to 100%%' USING ERRCODE = '23514';
        END IF;
        IF v_listed IS DISTINCT FROM v_people THEN
            RAISE EXCEPTION 'the list must say how many people it holds' USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_kyc_next_of_kin_shares() FROM PUBLIC",
    "CREATE CONSTRAINT TRIGGER kyc_next_of_kin_shares AFTER INSERT OR UPDATE OR DELETE "
    "ON kyc_next_of_kin DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_next_of_kin_shares()",
    "CREATE CONSTRAINT TRIGGER kyc_next_of_kin_lists_shares AFTER INSERT OR UPDATE "
    "ON kyc_next_of_kin_lists DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW EXECUTE FUNCTION finvest_kyc_next_of_kin_shares()",
    "REVOKE ALL ON kyc_next_of_kin_lists, kyc_next_of_kin FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (people, updated_at) ON kyc_next_of_kin_lists TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, DELETE ON kyc_next_of_kin TO {APP_ROLE}",
    "ALTER TABLE kyc_next_of_kin_lists ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE kyc_next_of_kin ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY kyc_next_of_kin_lists_read ON kyc_next_of_kin_lists FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY kyc_next_of_kin_lists_insert ON kyc_next_of_kin_lists FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'client' "
    "AND client_id = finvest_actor_id())",
    f"CREATE POLICY kyc_next_of_kin_lists_update ON kyc_next_of_kin_lists FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY kyc_next_of_kin_read ON kyc_next_of_kin FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY kyc_next_of_kin_insert ON kyc_next_of_kin FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY kyc_next_of_kin_delete ON kyc_next_of_kin FOR DELETE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(SHARES)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_next_of_kin_lists) THEN
        RAISE EXCEPTION 'refusing to drop next of kin while clients have saved them';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE kyc_next_of_kin")
    op.execute("DROP TABLE kyc_next_of_kin_lists")
    op.execute("DROP FUNCTION finvest_kyc_next_of_kin_shares()")
