"""Next of kin are contacts only: drop their shares.

Revision ID: 0234_kyc_next_of_kin_contacts
Revises: 0270_payout_wallet_short

The owner decided that next of kin are people Finvest contacts, with no share of what the client
holds. This drops `share_bps`, its check and the rule that shares add up to 100%. The rule that a
list says how many people it holds stays, under a new name.

The downgrade refuses while any client has saved a list: the old rule needs a share for every
person, and there is no honest share to put back.

See docs/client-profile.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0234_kyc_next_of_kin_contacts"
down_revision: str | None = "0270_payout_wallet_short"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Runs at commit, once the whole list is in place, so a save may delete and insert in any order.
COUNT = """
CREATE FUNCTION finvest_kyc_next_of_kin_count() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        v_client push_id := COALESCE(NEW.client_id, OLD.client_id);
        v_people integer;
        v_listed smallint;
    BEGIN
        SELECT count(*) INTO v_people FROM kyc_next_of_kin WHERE client_id = v_client;
        SELECT people INTO v_listed FROM kyc_next_of_kin_lists WHERE client_id = v_client;
        IF v_listed IS DISTINCT FROM v_people THEN
            RAISE EXCEPTION 'the list must say how many people it holds' USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

# As migration 0230 created it.
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


def _triggers(name: str, function: str) -> list[str]:
    return [
        f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC",
        f"CREATE CONSTRAINT TRIGGER kyc_next_of_kin_{name} AFTER INSERT OR UPDATE OR DELETE "
        "ON kyc_next_of_kin DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()",
        f"CREATE CONSTRAINT TRIGGER kyc_next_of_kin_lists_{name} AFTER INSERT OR UPDATE "
        "ON kyc_next_of_kin_lists DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()",
    ]


def _drop_triggers(name: str, function: str) -> list[str]:
    return [
        f"DROP TRIGGER kyc_next_of_kin_{name} ON kyc_next_of_kin",
        f"DROP TRIGGER kyc_next_of_kin_lists_{name} ON kyc_next_of_kin_lists",
        f"DROP FUNCTION {function}()",
    ]


def upgrade() -> None:
    for statement in _drop_triggers("shares", "finvest_kyc_next_of_kin_shares"):
        op.execute(statement)
    op.execute("ALTER TABLE kyc_next_of_kin DROP CONSTRAINT ck_kyc_next_of_kin_share")
    op.execute("ALTER TABLE kyc_next_of_kin DROP COLUMN share_bps")
    op.execute(COUNT)
    for statement in _triggers("count", "finvest_kyc_next_of_kin_count"):
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM kyc_next_of_kin_lists) THEN
        RAISE EXCEPTION 'refusing to put shares back on next of kin clients have saved';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in _drop_triggers("count", "finvest_kyc_next_of_kin_count"):
        op.execute(statement)
    op.execute("ALTER TABLE kyc_next_of_kin ADD COLUMN share_bps integer NOT NULL")
    op.execute(
        "ALTER TABLE kyc_next_of_kin ADD CONSTRAINT ck_kyc_next_of_kin_share "
        "CHECK (share_bps BETWEEN 1 AND 10000)"
    )
    op.execute(SHARES)
    for statement in _triggers("shares", "finvest_kyc_next_of_kin_shares"):
        op.execute(statement)
