"""Keep the latest call-back number when someone asks again while their request is open.

Revision ID: 0224_identity_recovery_asks
Revises: 0222_identity_recovery_requests

Under 0222 a second ask while a request was open recorded nothing, so a person who gave a wrong
number to call, or whose phone died, could not correct it, and staff called a dead number. Now a
later ask replaces the number to call and the reason on the open request, counts the ask, and
records when it came. The account is still told only once, when the request opens: the function
returns the client only for the first ask.

Staff see how many asks came in and when the last one did, so a request someone keeps asking
about, or one a stranger keeps changing, stands out. Staff still check who it is on the call.

The guard lets only the request function change an open request this way: the app's and the
worker's roles may still only close it.
"""

from alembic import op

revision = "0224_identity_recovery_asks"
down_revision = "0222_identity_recovery_requests"
branch_labels = None
depends_on = None
# The running release only writes these rows through identity_request_recovery, which this
# replaces in the same transaction, and reads named columns, so it keeps working beside it.
zero_downtime = True

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
SIGNATURE = "identity_request_recovery(text, text, text, text, timestamptz)"

REQUEST = """
CREATE OR REPLACE FUNCTION identity_request_recovery(
    p_id text, p_phone text, p_contact text, p_reason text, p_now timestamptz
) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        matched text;
        opened text;
        asked integer;
    BEGIN
        SELECT id INTO matched FROM public.identity_clients
        WHERE phone = p_phone AND status IN ('active', 'locked');
        IF matched IS NULL THEN
            INSERT INTO public.identity_recovery_requests
                (id, client_id, contact_phone, reason, status, created_at, asks, last_asked_at)
            VALUES (p_id, NULL, NULL, p_reason, 'no_account', p_now, 1, p_now);
            RETURN NULL;
        END IF;
        -- One open request per account. A later ask while it is open replaces the number to
        -- call and the reason, and is counted, but only the ask that opened it returns the
        -- client, so the account is told once, not once per ask.
        INSERT INTO public.identity_recovery_requests AS r
            (id, client_id, contact_phone, reason, status, created_at, asks, last_asked_at)
        VALUES (p_id, matched, p_contact, p_reason, 'open', p_now, 1, p_now)
        ON CONFLICT (client_id) WHERE status = 'open' DO UPDATE SET
            contact_phone = EXCLUDED.contact_phone,
            reason = EXCLUDED.reason,
            asks = r.asks + 1,
            last_asked_at = GREATEST(r.last_asked_at, EXCLUDED.last_asked_at)
        RETURNING r.client_id, r.asks INTO opened, asked;
        IF asked = 1 THEN
            RETURN opened;
        END IF;
        RETURN NULL;
    END;
    $$
"""

GUARD = f"""
CREATE OR REPLACE FUNCTION identity_recovery_requests_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        -- A later ask, through the request function only: a new number to call and reason.
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}')
            AND OLD.status = 'open' AND NEW.status = 'open'
            AND NEW.asks = OLD.asks + 1
            AND (to_jsonb(NEW) - '{{contact_phone,reason,asks,last_asked_at}}'::text[])
                = (to_jsonb(OLD) - '{{contact_phone,reason,asks,last_asked_at}}'::text[]) THEN
            RETURN NEW;
        END IF;
        IF OLD.status <> 'open' OR NEW.status <> 'closed'
            OR NEW.closed_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{{status,closed_at,closed_by,outcome}}'::text[])
                <> (to_jsonb(OLD) - '{{status,closed_at,closed_by,outcome}}'::text[]) THEN
            RAISE EXCEPTION 'a recovery request is only closed, once, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

OLD_REQUEST = """
CREATE OR REPLACE FUNCTION identity_request_recovery(
    p_id text, p_phone text, p_contact text, p_reason text, p_now timestamptz
) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        matched text;
        opened text;
    BEGIN
        SELECT id INTO matched FROM public.identity_clients
        WHERE phone = p_phone AND status IN ('active', 'locked');
        IF matched IS NULL THEN
            INSERT INTO public.identity_recovery_requests
                (id, client_id, contact_phone, reason, status, created_at)
            VALUES (p_id, NULL, NULL, p_reason, 'no_account', p_now);
            RETURN NULL;
        END IF;
        -- One open request per account. While one is open, a new ask records nothing and
        -- returns nothing, so the account is told once, not once per ask.
        INSERT INTO public.identity_recovery_requests
            (id, client_id, contact_phone, reason, status, created_at)
        VALUES (p_id, matched, p_contact, p_reason, 'open', p_now)
        ON CONFLICT (client_id) WHERE status = 'open' DO NOTHING
        RETURNING client_id INTO opened;
        RETURN opened;
    END;
    $$
"""

OLD_GUARD = """
CREATE OR REPLACE FUNCTION identity_recovery_requests_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF OLD.status <> 'open' OR NEW.status <> 'closed'
            OR NEW.closed_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,closed_at,closed_by,outcome}'::text[])
                <> (to_jsonb(OLD) - '{status,closed_at,closed_by,outcome}'::text[]) THEN
            RAISE EXCEPTION 'a recovery request is only closed, once, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    "ALTER TABLE identity_recovery_requests "
    "ADD COLUMN asks integer NOT NULL DEFAULT 1, ADD COLUMN last_asked_at timestamptz",
    "UPDATE identity_recovery_requests SET last_asked_at = created_at",
    "ALTER TABLE identity_recovery_requests ALTER COLUMN last_asked_at SET NOT NULL",
    "ALTER TABLE identity_recovery_requests ADD CONSTRAINT ck_identity_recovery_requests_asks "
    "CHECK (asks >= 1 AND last_asked_at >= created_at)",
    REQUEST,
    GUARD,
    f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {APP_ROLE}",
]

# Going back would lose the later asks' count on a request staff have not followed up yet.
LATER_ASKS_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_recovery_requests WHERE status = 'open' AND asks > 1) THEN
        RAISE EXCEPTION 'refusing to drop the later asks on open recovery requests';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(LATER_ASKS_GUARD)
    op.execute(OLD_GUARD)
    op.execute(OLD_REQUEST)
    op.execute(
        "ALTER TABLE identity_recovery_requests DROP CONSTRAINT ck_identity_recovery_requests_asks, "
        "DROP COLUMN asks, DROP COLUMN last_asked_at"
    )
