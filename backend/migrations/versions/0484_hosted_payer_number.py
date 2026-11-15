"""Keep the number that paid on a hosted mobile money order, now that any number may pay.

Revision ID: 0484_hosted_payer_number
Revises: 0493_product_types

The owner decided on 2026-11-15 to accept money from any payer, keeping payouts to the client's
own name only. A mobile money payment on Pesapal's page is no longer held when a number other
than the client's paid it: it counts at once, matched to the client by its order. The number
Pesapal names as having paid is kept for screening and audit.

* `payment_card_orders.paid_from_msisdn`: that number, as `+256` and nine digits. Empty when the
  order is saved, filled in once by the worker when the payment is confirmed, and never changed.
  A card order never has one.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0484_hosted_payer_number"
down_revision: str | None = "0493_product_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKER_ROLE = "finvest_worker"

GUARD = """
CREATE OR REPLACE FUNCTION finvest_payment_card_orders_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        deposit record;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            SELECT client_id, method, currency, amount_minor INTO deposit
              FROM payment_deposits WHERE id = NEW.deposit_id;
            IF NOT FOUND OR deposit.method NOT IN ('card', 'mtn', 'airtel')
               OR deposit.client_id <> NEW.client_id
               OR deposit.currency <> NEW.currency
               OR NEW.total_minor <> deposit.amount_minor + NEW.charge_minor
               OR (deposit.method <> 'card' AND NEW.charge_minor <> 0)
               OR NEW.page_url IS NOT NULL OR NEW.card_brand IS NOT NULL
               OR NEW.card_last_four IS NOT NULL OR NEW.confirmation_code IS NOT NULL
               OR NEW.paid_from_msisdn IS NOT NULL THEN
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
               AND NEW.confirmation_code IS DISTINCT FROM OLD.confirmation_code)
           OR (OLD.paid_from_msisdn IS NOT NULL
               AND NEW.paid_from_msisdn IS DISTINCT FROM OLD.paid_from_msisdn) THEN
            RAISE EXCEPTION 'a card order is filled in once and never changed'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.paid_from_msisdn IS NOT NULL AND OLD.paid_from_msisdn IS NULL THEN
            SELECT method INTO deposit FROM payment_deposits WHERE id = NEW.deposit_id;
            IF deposit.method = 'card' THEN
                RAISE EXCEPTION 'a card order never keeps a number that paid'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    'ALTER TABLE payment_card_orders ADD COLUMN paid_from_msisdn varchar(13) COLLATE "C"',
    "ALTER TABLE payment_card_orders ADD CONSTRAINT ck_payment_card_orders_paid_from_msisdn "
    "CHECK (paid_from_msisdn ~ '^\\+256[0-9]{9}$')",
    GUARD,
    f"GRANT UPDATE (paid_from_msisdn) ON payment_card_orders TO {WORKER_ROLE}",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_card_orders WHERE paid_from_msisdn IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the numbers that paid hosted mobile money orders';
    END IF;
END
$$
"""


def _guard_of_0483() -> str:
    path = Path(__file__).with_name("0483_hosted_mobile_money.py")
    spec = importlib.util.spec_from_file_location("finvest_0483_hosted_mobile_money", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(
        module.guard(
            "'card', 'mtn', 'airtel'", "OR (deposit.method <> 'card' AND NEW.charge_minor <> 0)"
        )
    )


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the column would erase who paid money Finvest received.
    op.execute(EMPTY_GUARD)
    op.execute(f"REVOKE UPDATE (paid_from_msisdn) ON payment_card_orders FROM {WORKER_ROLE}")
    op.execute(_guard_of_0483())
    op.execute("ALTER TABLE payment_card_orders DROP COLUMN paid_from_msisdn")
