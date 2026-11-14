"""Let staff create, edit, pause and open products, each change approved by a second person.

Revision ID: 0360_investment_product_changes
Revises: 0270_payout_wallet_short

Until now products were seeded by migration 0060 and read-only for the application. A change is
now a row in `investment_product_changes`: one member of staff proposes the whole product as it
will be, and a different one approves it. Only an approval writes to `investment_products`, and
the database checks that itself:

* a product row is written only with `change_id` naming an approved change that holds exactly
  the values written, approved by the person the transaction acts for;
* an existing product moves on one version at a time, and only by a change made against the
  version it is at, so an approved change can never be replayed and a stale one cannot land;
* the code, currency and creation time of a product never change, and nothing removes one.

A new product's first rate is written with it, in the same approval. `investment_rates` gains
`product_change_id` for that rate, and its guard accepts a rate whose product was created by
that approved change, with the change's own rate and tax. Every other rate still needs an
approved rate proposal. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0360_investment_product_changes"
down_revision: str | None = "0270_payout_wallet_short"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE investment_product_changes (
    id push_id NOT NULL,
    action varchar(8) NOT NULL,
    -- The product changed. NULL for a new product, which has no row until it is approved.
    product_id push_id,
    -- The version of the product the change was made against. NULL for a new product.
    base_version integer,
    -- The whole product as it will be once the change is approved.
    code varchar(24) COLLATE "C" NOT NULL,
    name varchar(80) NOT NULL,
    kind varchar(20) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    term_days integer,
    minimum_minor bigint NOT NULL,
    interest_paid varchar(16) NOT NULL,
    product_status varchar(8) NOT NULL,
    -- A new product's first rate. It starts on the day the product is approved.
    first_annual_rate_bps integer,
    first_tax_bps integer,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module. No foreign key: a module owns only its own tables.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    CONSTRAINT pk_investment_product_changes PRIMARY KEY (id),
    CONSTRAINT fk_investment_product_changes_product
        FOREIGN KEY (product_id) REFERENCES investment_products (id),
    CONSTRAINT ck_investment_product_changes_action
        CHECK (action IN ('create', 'edit', 'pause', 'open')),
    CONSTRAINT ck_investment_product_changes_create CHECK (
        (action = 'create') = (product_id IS NULL)
        AND (product_id IS NULL) = (base_version IS NULL)
        AND (action = 'create') = (first_annual_rate_bps IS NOT NULL)
        AND (first_annual_rate_bps IS NULL) = (first_tax_bps IS NULL)
        AND (action <> 'create' OR product_status = 'draft')),
    CONSTRAINT ck_investment_product_changes_target_status CHECK (
        (action <> 'pause' OR product_status = 'paused')
        AND (action <> 'open' OR product_status = 'live')),
    CONSTRAINT ck_investment_product_changes_code
        CHECK (code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'),
    CONSTRAINT ck_investment_product_changes_name CHECK (btrim(name) = name AND name <> ''),
    CONSTRAINT ck_investment_product_changes_kind
        CHECK (kind IN ('treasury_bill', 'fixed_income')),
    CONSTRAINT ck_investment_product_changes_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_investment_product_changes_term CHECK (term_days BETWEEN 1 AND 3650),
    CONSTRAINT ck_investment_product_changes_minimum CHECK (minimum_minor > 0),
    CONSTRAINT ck_investment_product_changes_interest_paid
        CHECK (interest_paid IN ('at_maturity', 'monthly')),
    CONSTRAINT ck_investment_product_changes_product_status
        CHECK (product_status IN ('draft', 'live', 'paused')),
    CONSTRAINT ck_investment_product_changes_open_term
        CHECK (term_days IS NOT NULL OR interest_paid <> 'at_maturity'),
    CONSTRAINT ck_investment_product_changes_rate CHECK (first_annual_rate_bps BETWEEN 1 AND 5000),
    CONSTRAINT ck_investment_product_changes_tax CHECK (first_tax_bps BETWEEN 0 AND 9999),
    CONSTRAINT ck_investment_product_changes_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_investment_product_changes_status
        CHECK (status IN ('proposed', 'approved', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_investment_product_changes_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL) AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own change, and only the proposer withdraws it.
    CONSTRAINT ck_investment_product_changes_second_person CHECK (
        status NOT IN ('approved', 'sent_back') OR decided_by <> proposed_by),
    CONSTRAINT ck_investment_product_changes_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_investment_product_changes_note CHECK (
        status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> '')
);
-- One change waits for approval per product at a time, and per new code.
CREATE UNIQUE INDEX uq_investment_product_changes_open
    ON investment_product_changes (product_id) WHERE status = 'proposed' AND product_id IS NOT NULL;
CREATE UNIQUE INDEX uq_investment_product_changes_open_code
    ON investment_product_changes (code) WHERE status = 'proposed' AND action = 'create';
CREATE INDEX ix_investment_product_changes_proposed
    ON investment_product_changes (proposed_at DESC, id DESC);
ALTER TABLE investment_products ADD COLUMN version integer NOT NULL DEFAULT 1;
ALTER TABLE investment_products ADD COLUMN change_id push_id;
ALTER TABLE investment_products ADD CONSTRAINT fk_investment_products_change
    FOREIGN KEY (change_id) REFERENCES investment_product_changes (id);
ALTER TABLE investment_products ADD CONSTRAINT ck_investment_products_version CHECK (version >= 1);
ALTER TABLE investment_rates ADD COLUMN product_change_id push_id;
ALTER TABLE investment_rates ADD CONSTRAINT fk_investment_rates_product_change
    FOREIGN KEY (product_change_id) REFERENCES investment_product_changes (id);
ALTER TABLE investment_rates ADD CONSTRAINT uq_investment_rates_product_change
    UNIQUE (product_change_id);
ALTER TABLE investment_rates ADD CONSTRAINT ck_investment_rates_one_source
    CHECK (proposal_id IS NULL OR product_change_id IS NULL)
"""

# A change is written once, then decided once, by the member of staff the transaction acts for.
# What it changes never changes.
CHANGES_GUARD = """
CREATE FUNCTION finvest_investment_product_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff propose and decide product changes' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a product change starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided product change never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note}'::text[])
                <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note}'::text[]) THEN
            RAISE EXCEPTION 'a product change is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# The application writes a product only as an approved change says, one version at a time.
# Migrations, which run as the owner, may still seed and correct products.
PRODUCTS_GUARD = f"""
CREATE FUNCTION finvest_investment_products_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        change investment_product_changes%ROWTYPE;
    BEGIN
        IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
            RAISE EXCEPTION 'a product is never removed: pause it' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' OR NEW.change_id IS NULL THEN
            RAISE EXCEPTION 'a product changes only by an approved change' USING ERRCODE = '42501';
        END IF;
        SELECT * INTO change FROM investment_product_changes WHERE id = NEW.change_id FOR SHARE;
        IF NOT FOUND OR change.status <> 'approved'
            OR change.decided_by IS DISTINCT FROM finvest_actor_id()
            OR change.code <> NEW.code OR change.name <> NEW.name OR change.kind <> NEW.kind
            OR change.currency <> NEW.currency
            OR change.term_days IS DISTINCT FROM NEW.term_days
            OR change.minimum_minor <> NEW.minimum_minor
            OR change.interest_paid <> NEW.interest_paid
            OR change.product_status <> NEW.status THEN
            RAISE EXCEPTION 'a product must match its approved change' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF change.action <> 'create' OR NEW.version <> 1 THEN
                RAISE EXCEPTION 'a new product comes from an approved new product'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF change.action = 'create' OR change.product_id <> OLD.id
            OR change.base_version <> OLD.version OR NEW.version <> OLD.version + 1
            OR NEW.change_id IS NOT DISTINCT FROM OLD.change_id
            OR NEW.id <> OLD.id OR NEW.code <> OLD.code OR NEW.currency <> OLD.currency
            OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'a product moves on one approved change at a time'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names


def rates_guard(with_product_changes: bool) -> str:
    # The rate a new product is created with: its product came from this very change.
    first_rate = """
        IF NEW.product_change_id IS NOT NULL THEN
            SELECT * INTO change FROM investment_product_changes
                WHERE id = NEW.product_change_id FOR SHARE;
            IF NOT FOUND OR change.status <> 'approved' OR change.action <> 'create'
                OR NOT EXISTS (SELECT FROM investment_products
                    WHERE id = NEW.product_id AND change_id = change.id)
                OR change.first_annual_rate_bps <> NEW.annual_rate_bps
                OR change.first_tax_bps <> NEW.tax_bps THEN
                RAISE EXCEPTION 'a first rate must match its approved new product'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;"""
    return f"""
CREATE OR REPLACE FUNCTION finvest_investment_rates_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        proposal investment_rate_proposals%ROWTYPE;
        {"change investment_product_changes%ROWTYPE;" if with_product_changes else ""}
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a rate is never changed or removed' USING ERRCODE = '42501';
        END IF;{first_rate if with_product_changes else ""}
        IF NEW.proposal_id IS NULL THEN
            IF current_user IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
                RAISE EXCEPTION 'a rate needs an approved proposal' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        SELECT * INTO proposal FROM investment_rate_proposals WHERE id = NEW.proposal_id FOR SHARE;
        IF NOT FOUND OR proposal.status <> 'approved'
            OR proposal.product_id <> NEW.product_id
            OR proposal.effective_from <> NEW.effective_from
            OR proposal.annual_rate_bps <> NEW.annual_rate_bps
            OR proposal.tax_bps <> NEW.tax_bps THEN
            RAISE EXCEPTION 'a rate must match its approved proposal' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names


# Checked at commit: an approval that did not write its product, or a new product's first rate,
# is refused, so "approved" always means the catalogue says what the change says.
APPLIED = """
CREATE FUNCTION finvest_investment_product_changes_applied() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF NOT EXISTS (SELECT FROM investment_products WHERE change_id = NEW.id)
            OR (NEW.action = 'create'
                AND NOT EXISTS (SELECT FROM investment_rates WHERE product_change_id = NEW.id)) THEN
            RAISE EXCEPTION 'an approved product change must be written to the catalogue'
                USING ERRCODE = '42501';
        END IF;
        RETURN NULL;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_investment_product_changes_applied() FROM PUBLIC",
    "CREATE CONSTRAINT TRIGGER investment_product_changes_applied AFTER UPDATE "
    "ON investment_product_changes DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "WHEN (NEW.status = 'approved') "
    "EXECUTE FUNCTION finvest_investment_product_changes_applied()",
    "REVOKE ALL ON FUNCTION finvest_investment_product_changes_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_product_changes_guard BEFORE INSERT OR UPDATE "
    "ON investment_product_changes FOR EACH ROW "
    "EXECUTE FUNCTION finvest_investment_product_changes_guard()",
    "REVOKE ALL ON FUNCTION finvest_investment_products_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_products_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON investment_products FOR EACH ROW EXECUTE FUNCTION finvest_investment_products_guard()",
    "CREATE TRIGGER investment_products_no_truncate BEFORE TRUNCATE ON investment_products "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_investment_products_guard()",
    "REVOKE ALL ON investment_product_changes FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) "
    f"ON investment_product_changes TO {APP_ROLE}",
    f"GRANT SELECT ON investment_product_changes TO {WORKER_ROLE}",
    f"GRANT INSERT, UPDATE (name, kind, term_days, minimum_minor, interest_paid, status, version, "
    f"change_id, updated_at) ON investment_products TO {APP_ROLE}",
    "ALTER TABLE investment_product_changes ENABLE ROW LEVEL SECURITY",
    # Changes are staff business. Which staff may propose or approve is a permission, checked
    # in the application. The worker reads them for nothing yet, but may.
    f"CREATE POLICY investment_product_changes_staff ON investment_product_changes "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff') "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY investment_product_changes_worker ON investment_product_changes "
    f"FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY investment_products_insert ON investment_products FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY investment_products_update ON investment_products FOR UPDATE TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff') WITH CHECK (finvest_actor_kind() = 'staff')",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [
        *split(TABLES),
        CHANGES_GUARD,
        PRODUCTS_GUARD,
        APPLIED,
        rates_guard(with_product_changes=True),
        *RULES,
    ]:
        op.execute(statement)


# Anything staff created or changed cannot be rebuilt by a migration, so it is never dropped.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_product_changes) THEN
        RAISE EXCEPTION 'refusing to drop product changes that staff proposed or approved';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(rates_guard(with_product_changes=False))
    for statement in [
        "DROP POLICY investment_products_update ON investment_products",
        "DROP POLICY investment_products_insert ON investment_products",
        f"REVOKE INSERT, UPDATE ON investment_products FROM {APP_ROLE}",
        "DROP TRIGGER investment_products_no_truncate ON investment_products",
        "DROP TRIGGER investment_products_guard ON investment_products",
        "DROP FUNCTION finvest_investment_products_guard()",
        "ALTER TABLE investment_rates DROP COLUMN product_change_id",
        "ALTER TABLE investment_products DROP COLUMN change_id",
        "ALTER TABLE investment_products DROP COLUMN version",
        "DROP TABLE investment_product_changes",
        "DROP FUNCTION finvest_investment_product_changes_applied()",
        "DROP FUNCTION finvest_investment_product_changes_guard()",
    ]:
        op.execute(statement)
