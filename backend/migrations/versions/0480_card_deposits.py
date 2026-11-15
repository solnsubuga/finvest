"""Add card payments: a card deposit, its order at the card provider and the card setting.

Revision ID: 0480_card_deposits
Revises: 0450_identity_passkey_login

A card payment is a deposit like any other, in the same table, so the one live deposit per
investment rule covers it. It has method `card`, a card provider (`pesapal`, or `card_sandbox`
outside production) and no payer number: the card is typed on the provider's own page, never
in our apps. It follows the mobile money path and the update guard of 0020 as it stands.

Its order is a row of its own, `payment_card_orders`, saved by the client with the deposit:

* What the client pays on top (the card charge) and the total the provider collects never
  change. The total is the deposit's amount and the charge, checked against the deposit.
* The provider's payment page, and the card's brand, last four digits and the provider's
  confirmation code, are filled in by the worker once each and never changed. No card number,
  expiry or security code has a column anywhere.
* No row is ever deleted. A client sees their own, staff see all, the worker sees all.

The platform setting `card_payments` holds the card charge rate and the card limits.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0480_card_deposits"
down_revision: str | None = "0450_identity_passkey_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

IS_STAFF = "finvest_actor_kind() = 'staff'"
IS_CLIENT = "finvest_actor_kind() = 'client'"
OWN = f"{IS_CLIENT} AND client_id = finvest_actor_id()"

OLD_KEYS = (
    "'payment_channels', 'public_holidays', 'bank_payout_cutoff', 'maturity_reminders', "
    "'staff_idle_minutes', 'office_networks', 'statement_layouts'"
)
KEYS = f"{OLD_KEYS}, 'card_payments'"
CARD_PROVIDERS = "'pesapal', 'card_sandbox'"

TABLE = """
CREATE TABLE payment_card_orders (
    deposit_id push_id NOT NULL,
    client_id push_id NOT NULL,
    currency varchar(3) NOT NULL,
    charge_minor bigint NOT NULL,
    total_minor bigint NOT NULL,
    page_url varchar(1024),
    card_brand varchar(24),
    card_last_four char(4),
    confirmation_code varchar(64),
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_payment_card_orders PRIMARY KEY (deposit_id),
    CONSTRAINT fk_payment_card_orders_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT ck_payment_card_orders_currency CHECK (currency = 'UGX'),
    CONSTRAINT ck_payment_card_orders_charge_minor CHECK (charge_minor >= 0),
    CONSTRAINT ck_payment_card_orders_total_minor CHECK (total_minor > charge_minor),
    -- https, or the local client app's address for the card sandbox in development.
    CONSTRAINT ck_payment_card_orders_page_url CHECK (page_url ~
        '^(https://[^\\s/]+|http://(localhost|127[.]0[.]0[.]1)(:[0-9]{1,5})?)(/[^\\s]*)?$'),
    CONSTRAINT ck_payment_card_orders_card_brand
        CHECK (card_brand ~ '^[A-Za-z][A-Za-z ]{0,23}$'),
    CONSTRAINT ck_payment_card_orders_card_last_four CHECK (card_last_four ~ '^[0-9]{4}$'),
    CONSTRAINT ck_payment_card_orders_confirmation_code
        CHECK (confirmation_code ~ '^[A-Za-z0-9._:-]{1,64}$')
)
"""

GUARD = """
CREATE FUNCTION finvest_payment_card_orders_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        deposit record;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            SELECT client_id, method, currency, amount_minor INTO deposit
              FROM payment_deposits WHERE id = NEW.deposit_id;
            IF NOT FOUND OR deposit.method <> 'card' OR deposit.client_id <> NEW.client_id
               OR deposit.currency <> NEW.currency
               OR NEW.total_minor <> deposit.amount_minor + NEW.charge_minor
               OR NEW.page_url IS NOT NULL OR NEW.card_brand IS NOT NULL
               OR NEW.card_last_four IS NOT NULL OR NEW.confirmation_code IS NOT NULL THEN
                RAISE EXCEPTION 'a card order must match its card deposit'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.deposit_id, NEW.client_id, NEW.currency, NEW.charge_minor, NEW.total_minor,
            NEW.created_at)
           IS DISTINCT FROM
           (OLD.deposit_id, OLD.client_id, OLD.currency, OLD.charge_minor, OLD.total_minor,
            OLD.created_at)
           OR (OLD.page_url IS NOT NULL AND NEW.page_url IS DISTINCT FROM OLD.page_url)
           OR (OLD.card_brand IS NOT NULL AND NEW.card_brand IS DISTINCT FROM OLD.card_brand)
           OR (OLD.card_last_four IS NOT NULL
               AND NEW.card_last_four IS DISTINCT FROM OLD.card_last_four)
           OR (OLD.confirmation_code IS NOT NULL
               AND NEW.confirmation_code IS DISTINCT FROM OLD.confirmation_code) THEN
            RAISE EXCEPTION 'a card order is filled in once and never changed'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_method",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_method "
    "CHECK (method IN ('mtn', 'airtel', 'bank_transfer', 'card'))",
    "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_payer_when",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_payer_when "
    "CHECK ((method IN ('bank_transfer', 'card')) = (payer_msisdn IS NULL))",
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_card_provider "
    f"CHECK ((method = 'card') = (provider IN ({CARD_PROVIDERS})))",
    TABLE.strip(),
    GUARD,
    "REVOKE ALL ON FUNCTION finvest_payment_card_orders_guard() FROM PUBLIC",
    "CREATE TRIGGER payment_card_orders_guard BEFORE INSERT OR UPDATE ON payment_card_orders "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payment_card_orders_guard()",
    "CREATE TRIGGER payment_card_orders_no_delete BEFORE DELETE ON payment_card_orders "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
    "CREATE TRIGGER payment_card_orders_no_truncate BEFORE TRUNCATE ON payment_card_orders "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payments_immutable()",
    "ALTER TABLE payment_card_orders ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON payment_card_orders FROM PUBLIC",
    f"GRANT SELECT, INSERT ON payment_card_orders TO {APP_ROLE}",
    f"GRANT SELECT ON payment_card_orders TO {WORKER_ROLE}",
    "GRANT UPDATE (page_url, card_brand, card_last_four, confirmation_code) "
    f"ON payment_card_orders TO {WORKER_ROLE}",
    "CREATE POLICY payment_card_orders_app_read ON payment_card_orders FOR SELECT "
    f"TO {APP_ROLE} USING ({IS_STAFF} OR ({OWN}))",
    "CREATE POLICY payment_card_orders_app_insert ON payment_card_orders FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({OWN})",
    f"CREATE POLICY payment_card_orders_worker ON payment_card_orders TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
    "ALTER TABLE platform_settings DROP CONSTRAINT ck_platform_settings_key",
    f"ALTER TABLE platform_settings ADD CONSTRAINT ck_platform_settings_key CHECK (key IN ({KEYS}))",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_deposits WHERE method = 'card')
       OR EXISTS (SELECT FROM platform_settings WHERE key = 'card_payments') THEN
        RAISE EXCEPTION 'refusing to remove card payments that hold deposits or settings';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Dropping card orders would erase the record of money received by card.
    op.execute(EMPTY_GUARD)
    for statement in (
        "ALTER TABLE platform_settings DROP CONSTRAINT ck_platform_settings_key",
        f"ALTER TABLE platform_settings ADD CONSTRAINT ck_platform_settings_key "
        f"CHECK (key IN ({OLD_KEYS}))",
        "DROP TABLE payment_card_orders",
        "DROP FUNCTION finvest_payment_card_orders_guard()",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_card_provider",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_payer_when",
        "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_payer_when "
        "CHECK ((method = 'bank_transfer') = (payer_msisdn IS NULL))",
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_method",
        "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_method "
        "CHECK (method IN ('mtn', 'airtel', 'bank_transfer'))",
    ):
        op.execute(statement)
