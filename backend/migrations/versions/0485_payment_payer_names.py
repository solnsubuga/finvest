"""Keep the name the network holds for a number that paid on a client's behalf.

Revision ID: 0485_payment_payer_names
Revises: 0484_hosted_payer_number

The owner decided on 2026-11-15 to accept money from any payer and keep the payer's number and
name on record for screening and audit. Neither Pesapal's status read nor ioTec's collection
gives a payer's name, so when a mobile money payment is confirmed from a number other than the
client's own, the worker asks ioTec Verify for the name the network holds for that number, the
same lookup a payout number and a client's own phone get.

`payment_payer_names` holds one lookup per deposit. Only the worker, acting as the system,
saves it, in the transaction that confirms the payment, and only the worker records what ioTec
said. An answer is final, and a lookup is never asked again, because each costs a fee and the
payment has already counted. Staff can read them; clients cannot.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0485_payment_payer_names"
down_revision: str | None = "0484_hosted_payer_number"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = [
    """
CREATE TABLE payment_payer_names (
    deposit_id push_id NOT NULL,
    client_id push_id NOT NULL,
    -- The number that paid, as `+256` and nine digits.
    phone varchar(13) COLLATE "C" NOT NULL,
    status varchar(16) NOT NULL,
    request_id uuid,
    registered_name varchar(120),
    error_code varchar(64) COLLATE "C",
    created_at timestamptz NOT NULL,
    answered_at timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_payer_names PRIMARY KEY (deposit_id),
    CONSTRAINT fk_payment_payer_names_deposit FOREIGN KEY (deposit_id)
        REFERENCES payment_deposits (id),
    CONSTRAINT ck_payment_payer_names_phone CHECK (phone ~ '^\\+256[0-9]{9}$'),
    CONSTRAINT ck_payment_payer_names_status CHECK (
        status IN ('queued', 'sent', 'found', 'no_name', 'unanswered')),
    CONSTRAINT ck_payment_payer_names_name CHECK (
        (status = 'found') = (registered_name IS NOT NULL)
        AND (registered_name IS NULL OR length(btrim(registered_name)) > 0)),
    CONSTRAINT ck_payment_payer_names_request CHECK (
        status NOT IN ('sent', 'found', 'no_name') OR request_id IS NOT NULL),
    CONSTRAINT ck_payment_payer_names_answered CHECK (
        (status IN ('found', 'no_name', 'unanswered')) = (answered_at IS NOT NULL)),
    CONSTRAINT ck_payment_payer_names_error_code CHECK (
        error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$')
)
""",
    "CREATE INDEX ix_payment_payer_names_client ON payment_payer_names (client_id)",
]

GUARD = f"""
CREATE FUNCTION finvest_payment_payer_names_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        deposit record;
        step text;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'payer name lookups are never deleted' USING ERRCODE = '42501';
        END IF;
        IF NOT (finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}') THEN
            RAISE EXCEPTION 'only the worker records a payer name lookup' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT client_id, method, status INTO deposit
              FROM payment_deposits WHERE id = NEW.deposit_id;
            IF NOT FOUND OR deposit.method NOT IN ('mtn', 'airtel')
               OR deposit.client_id <> NEW.client_id
               OR NEW.status <> 'queued' OR NEW.request_id IS NOT NULL
               OR NEW.registered_name IS NOT NULL OR NEW.error_code IS NOT NULL
               OR NEW.answered_at IS NOT NULL THEN
                RAISE EXCEPTION 'a payer name lookup must start queued, for a mobile money payment'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.deposit_id, NEW.client_id, NEW.phone, NEW.created_at)
               IS DISTINCT FROM (OLD.deposit_id, OLD.client_id, OLD.phone, OLD.created_at) THEN
            RAISE EXCEPTION 'a payer name lookup is for one payment, once' USING ERRCODE = '42501';
        END IF;
        IF OLD.request_id IS NOT NULL AND NEW.request_id IS DISTINCT FROM OLD.request_id THEN
            RAISE EXCEPTION 'a payer name lookup keeps its request' USING ERRCODE = '42501';
        END IF;
        step := OLD.status || '>' || NEW.status;
        IF step NOT IN ('queued>sent', 'queued>unanswered', 'sent>found', 'sent>no_name',
                        'sent>unanswered') THEN
            RAISE EXCEPTION 'an answered payer name lookup is final' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payment_payer_names_guard() FROM PUBLIC",
    "CREATE TRIGGER payment_payer_names_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON payment_payer_names FOR EACH ROW EXECUTE FUNCTION finvest_payment_payer_names_guard()",
    "CREATE TRIGGER payment_payer_names_no_truncate BEFORE TRUNCATE ON payment_payer_names "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payment_payer_names_guard()",
    "ALTER TABLE payment_payer_names ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON payment_payer_names FROM PUBLIC",
    f"GRANT SELECT ON payment_payer_names TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON payment_payer_names TO {WORKER_ROLE}",
    f"CREATE POLICY payment_payer_names_staff_read ON payment_payer_names FOR SELECT "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY payment_payer_names_worker ON payment_payer_names TO {WORKER_ROLE} "
    "USING (finvest_actor_kind() = 'system') WITH CHECK (finvest_actor_kind() = 'system')",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_payer_names) THEN
        RAISE EXCEPTION 'refusing to drop payer name lookups';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in [*TABLE, GUARD, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Who paid money Finvest received is kept for screening and audit; never erased here.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE payment_payer_names")
    op.execute("DROP FUNCTION finvest_payment_payer_names_guard()")
