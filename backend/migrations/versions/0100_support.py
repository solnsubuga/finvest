"""Add help chat and the complaints register.

Revision ID: 0100_support
Revises: 0080_dealing_orders

The support module owns these tables. See docs/support.md.

* `support_conversations` and `support_messages` are the help chat between a client and staff.
  A client has one open conversation at a time. Messages are never changed or deleted.
* `support_saved_replies` are the answers staff reuse.
* `complaints` and `complaint_events` are the regulated register, kept apart from chat. Every
  complaint has a reference, a date by which Finvest promised to answer and, once closed, an
  outcome, what was done and whether the client accepted it. A closed complaint never changes,
  and nothing in the register can be deleted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0100_support"
down_revision: str | None = "0080_dealing_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE support_conversations (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    topic varchar(16) NOT NULL DEFAULT 'other',
    status varchar(8) NOT NULL DEFAULT 'open',
    created_at timestamptz NOT NULL,
    last_message_at timestamptz NOT NULL,
    -- When the client last wrote with no answer since. NULL once staff have replied.
    waiting_since timestamptz,
    client_read_at timestamptz,
    staff_read_at timestamptz,
    solved_at timestamptz,
    solved_by push_id,
    CONSTRAINT pk_support_conversations PRIMARY KEY (id),
    CONSTRAINT ck_support_conversations_topic CHECK (topic IN (
        'deposit', 'withdrawal', 'investment', 'identity', 'account', 'other')),
    CONSTRAINT ck_support_conversations_status CHECK (status IN ('open', 'solved')),
    CONSTRAINT ck_support_conversations_solved CHECK (
        (status = 'solved') = (solved_at IS NOT NULL)
        AND (status = 'solved') = (solved_by IS NOT NULL))
);
-- One open conversation per client: the app shows one chat, not a list of tickets.
CREATE UNIQUE INDEX uq_support_conversations_open
    ON support_conversations (client_id) WHERE status = 'open';
CREATE INDEX ix_support_conversations_client ON support_conversations (client_id, created_at DESC);
CREATE INDEX ix_support_conversations_inbox
    ON support_conversations (waiting_since, id) WHERE status = 'open';

CREATE TABLE support_messages (
    id push_id NOT NULL,
    conversation_id push_id NOT NULL,
    author_kind varchar(8) NOT NULL,
    author_id push_id NOT NULL,
    -- How the author is named to the other side. A member of staff is a first name only.
    author_name varchar(40),
    body varchar(2000) NOT NULL,
    -- Chosen by the sender, so a message sent again on a weak connection is stored once.
    client_key varchar(64) COLLATE "C" NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_support_messages PRIMARY KEY (id),
    CONSTRAINT fk_support_messages_conversation
        FOREIGN KEY (conversation_id) REFERENCES support_conversations (id),
    CONSTRAINT uq_support_messages_key UNIQUE (author_id, client_key),
    CONSTRAINT ck_support_messages_author_kind CHECK (author_kind IN ('client', 'staff')),
    CONSTRAINT ck_support_messages_author_name
        CHECK ((author_kind = 'staff') = (author_name IS NOT NULL)),
    CONSTRAINT ck_support_messages_body CHECK (btrim(body) <> ''),
    CONSTRAINT ck_support_messages_client_key CHECK (client_key ~ '^[A-Za-z0-9._:-]{8,64}$')
);
CREATE INDEX ix_support_messages_conversation
    ON support_messages (conversation_id, created_at, id);

CREATE TABLE support_saved_replies (
    id push_id NOT NULL,
    title varchar(60) NOT NULL,
    body varchar(2000) NOT NULL,
    created_by push_id NOT NULL,
    created_at timestamptz NOT NULL,
    retired_at timestamptz,
    retired_by push_id,
    CONSTRAINT pk_support_saved_replies PRIMARY KEY (id),
    CONSTRAINT ck_support_saved_replies_text CHECK (btrim(title) <> '' AND btrim(body) <> ''),
    CONSTRAINT ck_support_saved_replies_retired
        CHECK ((retired_at IS NULL) = (retired_by IS NULL))
);

CREATE SEQUENCE complaint_numbers AS integer START WITH 1001 NO CYCLE;

CREATE TABLE complaints (
    id push_id NOT NULL,
    -- What the client and the regulator quote. Never reused.
    reference varchar(12) COLLATE "C" NOT NULL
        DEFAULT ('C-' || nextval('complaint_numbers')::text),
    client_id push_id NOT NULL,
    -- The chat the complaint came out of, if it did.
    conversation_id push_id,
    category varchar(16) NOT NULL,
    received_via varchar(12) NOT NULL,
    summary varchar(120) NOT NULL,
    details varchar(4000) NOT NULL,
    amount_minor bigint,
    amount_currency varchar(3),
    status varchar(20) NOT NULL DEFAULT 'new',
    owner_id push_id,
    received_at timestamptz NOT NULL,
    -- The Kampala calendar day by which Finvest promised to answer.
    due_on date NOT NULL,
    logged_by_kind varchar(8) NOT NULL,
    logged_by push_id NOT NULL,
    escalated_at timestamptz,
    closed_at timestamptz,
    closed_by push_id,
    outcome varchar(16),
    resolution varchar(2000),
    client_accepted boolean,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_complaints PRIMARY KEY (id),
    CONSTRAINT uq_complaints_reference UNIQUE (reference),
    CONSTRAINT fk_complaints_conversation
        FOREIGN KEY (conversation_id) REFERENCES support_conversations (id),
    CONSTRAINT ck_complaints_reference CHECK (reference ~ '^C-[0-9]{4,9}$'),
    CONSTRAINT ck_complaints_category CHECK (category IN (
        'payments', 'withdrawals', 'investments', 'identity', 'account', 'fees', 'service',
        'other')),
    CONSTRAINT ck_complaints_received_via CHECK (received_via IN (
        'app', 'chat', 'phone', 'email', 'in_person', 'letter')),
    CONSTRAINT ck_complaints_text CHECK (btrim(summary) <> '' AND btrim(details) <> ''),
    CONSTRAINT ck_complaints_amount CHECK (
        (amount_minor IS NULL) = (amount_currency IS NULL)
        AND amount_minor > 0 AND amount_currency IN ('UGX', 'USD')),
    CONSTRAINT ck_complaints_status CHECK (status IN (
        'new', 'investigating', 'waiting_for_client', 'escalated', 'closed')),
    CONSTRAINT ck_complaints_logged_by_kind CHECK (logged_by_kind IN ('client', 'staff')),
    -- A client logs their own complaint, from the app.
    CONSTRAINT ck_complaints_logged_by_client CHECK (
        logged_by_kind <> 'client' OR (logged_by = client_id AND received_via = 'app')),
    -- Closing records the outcome, what was done and whether the client accepted it.
    CONSTRAINT ck_complaints_closed CHECK (
        (status = 'closed') = (closed_at IS NOT NULL)
        AND (status = 'closed') = (closed_by IS NOT NULL)
        AND (status = 'closed') = (outcome IS NOT NULL)
        AND (status = 'closed') = (resolution IS NOT NULL)
        AND (status = 'closed') = (client_accepted IS NOT NULL)),
    CONSTRAINT ck_complaints_outcome
        CHECK (outcome IN ('upheld', 'partly_upheld', 'not_upheld') AND btrim(resolution) <> '')
);
CREATE INDEX ix_complaints_client ON complaints (client_id, received_at DESC);
CREATE INDEX ix_complaints_open ON complaints (due_on, id) WHERE status <> 'closed';
CREATE INDEX ix_complaints_received_at ON complaints (received_at DESC, id DESC);

CREATE TABLE complaint_events (
    id push_id NOT NULL,
    complaint_id push_id NOT NULL,
    action varchar(16) NOT NULL,
    actor_kind varchar(8) NOT NULL,
    actor_id push_id NOT NULL,
    from_status varchar(20),
    to_status varchar(20),
    note varchar(2000),
    occurred_at timestamptz NOT NULL,
    CONSTRAINT pk_complaint_events PRIMARY KEY (id),
    CONSTRAINT fk_complaint_events_complaint FOREIGN KEY (complaint_id) REFERENCES complaints (id),
    CONSTRAINT ck_complaint_events_action CHECK (action IN (
        'logged', 'assigned', 'status', 'note', 'escalated', 'closed')),
    CONSTRAINT ck_complaint_events_actor_kind CHECK (actor_kind IN ('client', 'staff'))
);
CREATE INDEX ix_complaint_events_complaint ON complaint_events (complaint_id, occurred_at, id)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
GUARDS = [
    """
CREATE FUNCTION finvest_support_conversations_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF OLD.status = 'solved' THEN
            RAISE EXCEPTION 'a solved conversation never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.id <> OLD.id OR NEW.client_id <> OLD.client_id OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a conversation cannot move to another client' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() = 'client' AND (
            NEW.status <> OLD.status OR NEW.topic <> OLD.topic
            OR NEW.staff_read_at IS DISTINCT FROM OLD.staff_read_at) THEN
            RAISE EXCEPTION 'only staff solve or file a conversation' USING ERRCODE = '42501';
        END IF;
        -- A client can only move their place in the staff queue forward in time, so a bug in
        -- the app cannot jump the queue: the time of the last message never goes back, a
        -- waiting time once set stays, and a new one is no earlier than the last message.
        IF public.finvest_actor_kind() = 'client' AND (
            NEW.last_message_at < OLD.last_message_at
            OR (OLD.waiting_since IS NOT NULL
                AND NEW.waiting_since IS DISTINCT FROM OLD.waiting_since)
            OR (OLD.waiting_since IS NULL AND NEW.waiting_since < OLD.last_message_at)) THEN
            RAISE EXCEPTION 'a client cannot move a conversation back in the queue'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'solved' AND NEW.solved_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a conversation is solved by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    """
CREATE FUNCTION finvest_complaints_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF OLD.status = 'closed' THEN
            RAISE EXCEPTION 'a closed complaint never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'owner_id', 'escalated_at', 'closed_at', 'closed_by',
                'outcome', 'resolution', 'client_accepted', 'updated_at'])
            <> (to_jsonb(OLD) - ARRAY['status', 'owner_id', 'escalated_at', 'closed_at',
                'closed_by', 'outcome', 'resolution', 'client_accepted', 'updated_at']) THEN
            RAISE EXCEPTION 'what a complaint said when it was logged never changes'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'closed' AND NEW.closed_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a complaint is closed by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
""",
]

_OWN = "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()"
_STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_support_conversations_guard(), finvest_complaints_guard() "
    "FROM PUBLIC",
    "CREATE TRIGGER support_conversations_guard BEFORE UPDATE ON support_conversations "
    "FOR EACH ROW EXECUTE FUNCTION finvest_support_conversations_guard()",
    "CREATE TRIGGER complaints_guard BEFORE UPDATE ON complaints "
    "FOR EACH ROW EXECUTE FUNCTION finvest_complaints_guard()",
    "REVOKE ALL ON support_conversations, support_messages, support_saved_replies, complaints, "
    "complaint_events FROM PUBLIC",
    "REVOKE ALL ON SEQUENCE complaint_numbers FROM PUBLIC",
    # No DELETE for anyone. The worker has no business in help chat or the register yet.
    f"GRANT SELECT, INSERT, UPDATE (topic, status, last_message_at, waiting_since, client_read_at, "
    f"staff_read_at, solved_at, solved_by) ON support_conversations TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON support_messages TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (retired_at, retired_by) ON support_saved_replies TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, owner_id, escalated_at, closed_at, closed_by, outcome, "
    f"resolution, client_accepted, updated_at) ON complaints TO {APP_ROLE}",
    f"GRANT USAGE ON SEQUENCE complaint_numbers TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON complaint_events TO {APP_ROLE}",
    "ALTER TABLE support_conversations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE support_messages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE support_saved_replies ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE complaints ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE complaint_events ENABLE ROW LEVEL SECURITY",
    # A client reaches their own chat. Staff reach every chat; which staff may is a permission
    # checked in the application.
    f"CREATE POLICY support_conversations_read ON support_conversations FOR SELECT TO {APP_ROLE} "
    f"USING (({_OWN}) OR {_STAFF})",
    f"CREATE POLICY support_conversations_insert ON support_conversations FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({_OWN} AND status = 'open')",
    f"CREATE POLICY support_conversations_update ON support_conversations FOR UPDATE TO {APP_ROLE} "
    f"USING (({_OWN}) OR {_STAFF})",
    # The subquery runs under the policies above, so a client sees the messages of their own
    # chat. Everyone writes only under their own name, and a client only into their open chat.
    f"CREATE POLICY support_messages_read ON support_messages FOR SELECT TO {APP_ROLE} "  # noqa: S608
    "USING (conversation_id IN (SELECT id FROM support_conversations))",
    f"CREATE POLICY support_messages_insert ON support_messages FOR INSERT TO {APP_ROLE} "  # noqa: S608
    "WITH CHECK (author_kind = finvest_actor_kind() AND author_id = finvest_actor_id() "
    "AND conversation_id IN (SELECT id FROM support_conversations WHERE status = 'open'))",
    f"CREATE POLICY support_saved_replies_staff ON support_saved_replies TO {APP_ROLE} "
    f"USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY complaints_read ON complaints FOR SELECT TO {APP_ROLE} "
    f"USING (({_OWN}) OR {_STAFF})",
    f"CREATE POLICY complaints_insert ON complaints FOR INSERT TO {APP_ROLE} WITH CHECK ("
    f"status = 'new' AND logged_by = finvest_actor_id() AND logged_by_kind = finvest_actor_kind() "
    f"AND (({_OWN}) OR {_STAFF}))",
    f"CREATE POLICY complaints_update ON complaints FOR UPDATE TO {APP_ROLE} USING ({_STAFF})",
    # The history holds staff notes, so only staff read it. A client sees the complaint itself.
    f"CREATE POLICY complaint_events_read ON complaint_events FOR SELECT TO {APP_ROLE} "
    f"USING ({_STAFF})",
    f"CREATE POLICY complaint_events_insert ON complaint_events FOR INSERT TO {APP_ROLE} "  # noqa: S608
    "WITH CHECK (actor_kind = finvest_actor_kind() AND actor_id = finvest_actor_id() "
    "AND complaint_id IN (SELECT id FROM complaints))",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *GUARDS, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM complaints) OR EXISTS (SELECT FROM support_conversations) THEN
        RAISE EXCEPTION 'refusing to drop help chat or complaints that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # The complaints register is a regulated record. A downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE complaint_events")
    op.execute("DROP TABLE complaints")
    op.execute("DROP SEQUENCE complaint_numbers")
    op.execute("DROP TABLE support_saved_replies")
    op.execute("DROP TABLE support_messages")
    op.execute("DROP TABLE support_conversations")
    op.execute("DROP FUNCTION finvest_complaints_guard()")
    op.execute("DROP FUNCTION finvest_support_conversations_guard()")
