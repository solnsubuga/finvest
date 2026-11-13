"""Change a client's mobile number only with two members of staff.

Revision ID: 0160_identity_phone_changes
Revises: 0082_dealing_maturities

A number is how a client proves who they are with a code, so whoever controls it could reset
the password or claim the account. One member of staff proposes a new number with a reason. A
different one approves it, and only then does the number change, through a function the API
cannot get around: the API role has no UPDATE on `identity_clients.phone`.
"""

from alembic import op

revision = "0160_identity_phone_changes"
down_revision = "0082_dealing_maturities"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE identity_phone_changes (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    new_phone text NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    CONSTRAINT pk_identity_phone_changes PRIMARY KEY (id),
    CONSTRAINT fk_identity_phone_changes_client
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT fk_identity_phone_changes_proposed_by
        FOREIGN KEY (proposed_by) REFERENCES identity_staff (id),
    CONSTRAINT fk_identity_phone_changes_decided_by
        FOREIGN KEY (decided_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_identity_phone_changes_phone CHECK (new_phone ~ '^\\+[1-9][0-9]{7,14}$'),
    CONSTRAINT ck_identity_phone_changes_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_identity_phone_changes_status
        CHECK (status IN ('proposed', 'approved', 'rejected')),
    CONSTRAINT ck_identity_phone_changes_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own proposal.
    CONSTRAINT ck_identity_phone_changes_second_person
        CHECK (status = 'proposed' OR decided_by <> proposed_by),
    CONSTRAINT ck_identity_phone_changes_expiry CHECK (expires_at > proposed_at)
);
-- One change waits for approval per client at a time.
CREATE UNIQUE INDEX uq_identity_phone_changes_open
    ON identity_phone_changes (client_id) WHERE status = 'proposed';
CREATE INDEX ix_identity_phone_changes_status
    ON identity_phone_changes (status, proposed_at DESC)
"""

# A proposal is written once, by the member of staff the transaction acts for, then decided
# once, by a different one. Its client, number and reason never change.
GUARD = """
CREATE FUNCTION identity_phone_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff propose and decide a new number' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a number change starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided number change never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note}'::text[])
                <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note}'::text[]) THEN
            RAISE EXCEPTION 'a number change is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# The one way the number changes. It needs a proposal approved by the person this transaction
# acts for, in this same transaction, so an approval cannot be replayed later. It also ends the
# client's sessions, voids codes already sent and limits withdrawals for a day. It returns the
# old number, so the caller can tell the client there. It runs as the table owner.
APPLY = """
CREATE FUNCTION identity_apply_phone_change(p_change_id text, p_restricted_until timestamptz)
    RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        change public.identity_phone_changes%ROWTYPE;
        old_phone text;
    BEGIN
        SELECT * INTO change FROM public.identity_phone_changes
        WHERE id = p_change_id AND status = 'approved'
            AND decided_by = public.finvest_actor_id()
            AND xmin = pg_current_xact_id()::xid
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'a number changes only with an approval in the same transaction'
                USING ERRCODE = '42501';
        END IF;
        SELECT phone INTO old_phone FROM public.identity_clients
        WHERE id = change.client_id AND status <> 'closed' FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'the client is closed or missing' USING ERRCODE = '42501';
        END IF;
        UPDATE public.identity_clients
        SET phone = change.new_phone,
            restricted_until = greatest(coalesce(restricted_until, p_restricted_until),
                                        p_restricted_until)
        WHERE id = change.client_id;
        UPDATE public.identity_sessions
        SET revoked_at = change.decided_at, revoked_reason = 'phone_changed'
        WHERE actor_kind = 'client' AND actor_id = change.client_id AND revoked_at IS NULL;
        UPDATE public.identity_codes SET consumed_at = change.decided_at
        WHERE actor_id = change.client_id AND consumed_at IS NULL;
        RETURN old_phone;
    END;
    $$
"""

GRANTS = [
    "REVOKE ALL ON identity_phone_changes FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE ON identity_phone_changes TO finvest_app",
    "REVOKE ALL ON FUNCTION identity_phone_changes_guard() FROM PUBLIC",
    "CREATE TRIGGER tr_identity_phone_changes_guard BEFORE INSERT OR UPDATE "
    "ON identity_phone_changes FOR EACH ROW EXECUTE FUNCTION identity_phone_changes_guard()",
    "REVOKE ALL ON FUNCTION identity_apply_phone_change(text, timestamptz) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION identity_apply_phone_change(text, timestamptz) TO finvest_app",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_phone_changes) THEN
        RAISE EXCEPTION 'refusing to drop the record of number changes';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(GUARD)
    op.execute(APPLY)
    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP FUNCTION identity_apply_phone_change(text, timestamptz)")
    op.execute("DROP TABLE identity_phone_changes")
    op.execute("DROP FUNCTION identity_phone_changes_guard()")
