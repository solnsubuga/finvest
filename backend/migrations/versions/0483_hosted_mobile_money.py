"""Let Pesapal collect mobile money on its hosted page, chosen on Settings.

Revision ID: 0483_hosted_mobile_money
Revises: 0482_provider_refunds

The owner decided on 2026-11-15 that Pesapal collects mobile money as well as cards, with ioTec
kept as the fallback. Pesapal cannot prompt a phone itself. A mobile money payment through it
is therefore an order on its hosted page, as a card payment is: the client picks MTN or Airtel
there and approves the prompt Pesapal sends.

* A mobile money deposit (`mtn` or `airtel`) may now have a hosted page provider (`pesapal`, or
  `card_sandbox` outside production). A card deposit still always has one. It keeps the
  client's own number as its payer, and the worker checks that this number paid.
* Its order is a row in `payment_card_orders`, as a card's is. It has no charge, because
  Finvest bears the provider's mobile money fee as it does ioTec's, so its total is the
  deposit's amount. Every other rule of 0480 still holds: the order is filled in once by the
  worker and never changed, and no row is ever deleted.
* The platform setting `mobile_money_collector` says who collects a client's mobile money
  payment: `iotec` (the default) or `pesapal`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0483_hosted_mobile_money"
down_revision: str | None = "0482_provider_refunds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEYS = (
    "'payment_channels', 'public_holidays', 'bank_payout_cutoff', 'maturity_reminders', "
    "'staff_idle_minutes', 'office_networks', 'statement_layouts', 'card_payments'"
)
KEYS = f"{OLD_KEYS}, 'mobile_money_collector'"
HOSTED = "'pesapal', 'card_sandbox'"


def guard(methods: str, charge_rule: str) -> str:
    """The card order guard of 0480, for the given deposit methods."""
    return f"""
CREATE OR REPLACE FUNCTION finvest_payment_card_orders_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        deposit record;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            SELECT client_id, method, currency, amount_minor INTO deposit
              FROM payment_deposits WHERE id = NEW.deposit_id;
            IF NOT FOUND OR deposit.method NOT IN ({methods})
               OR deposit.client_id <> NEW.client_id
               OR deposit.currency <> NEW.currency
               OR NEW.total_minor <> deposit.amount_minor + NEW.charge_minor
               {charge_rule}
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
"""  # noqa: S608 - constant method lists and rule


UPGRADE = [
    "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_card_provider",
    # A card always goes through a hosted page; a hosted page takes a card or mobile money.
    "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_card_provider CHECK ("
    f"(method <> 'card' OR provider IN ({HOSTED})) "
    f"AND (provider NOT IN ({HOSTED}) OR method IN ('card', 'mtn', 'airtel')))",
    # A mobile money order has no charge: Finvest bears the provider's fee.
    guard("'card', 'mtn', 'airtel'", "OR (deposit.method <> 'card' AND NEW.charge_minor <> 0)"),
    "ALTER TABLE platform_settings DROP CONSTRAINT ck_platform_settings_key",
    f"ALTER TABLE platform_settings ADD CONSTRAINT ck_platform_settings_key CHECK (key IN ({KEYS}))",
]

EMPTY_GUARD = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_deposits WHERE method IN ('mtn', 'airtel')
               AND provider IN ({HOSTED}))
       OR EXISTS (SELECT FROM platform_settings WHERE key = 'mobile_money_collector') THEN
        RAISE EXCEPTION 'refusing to remove hosted mobile money that holds payments or a setting';
    END IF;
END
$$
"""  # noqa: S608 - constant provider list


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in (
        "ALTER TABLE platform_settings DROP CONSTRAINT ck_platform_settings_key",
        "ALTER TABLE platform_settings ADD CONSTRAINT ck_platform_settings_key "
        f"CHECK (key IN ({OLD_KEYS}))",
        guard("'card'", ""),
        "ALTER TABLE payment_deposits DROP CONSTRAINT ck_payment_deposits_card_provider",
        "ALTER TABLE payment_deposits ADD CONSTRAINT ck_payment_deposits_card_provider "
        f"CHECK ((method = 'card') = (provider IN ({HOSTED})))",
    ):
        op.execute(statement)
