"""Add the product catalogue, its rates and rate proposals.

Revision ID: 0060_investment_products
Revises: 0020_payment_deposits

Products are seeded here and are read-only for the application: editing a product arrives with
its own approval flow. A rate is a dated row that is never changed or deleted. The application
can add one only for a proposal that a second member of staff approved, and the database checks
that itself. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0060_investment_products"
down_revision: str | None = "0020_payment_deposits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE investment_products (
    id push_id NOT NULL,
    code varchar(24) COLLATE "C" NOT NULL,
    name varchar(80) NOT NULL,
    kind varchar(20) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    -- Days from start to maturity. NULL is an open term.
    term_days integer,
    minimum_minor bigint NOT NULL,
    interest_paid varchar(16) NOT NULL,
    status varchar(8) NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_investment_products PRIMARY KEY (id),
    CONSTRAINT uq_investment_products_code UNIQUE (code),
    CONSTRAINT ck_investment_products_code CHECK (code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'),
    CONSTRAINT ck_investment_products_name CHECK (btrim(name) = name AND name <> ''),
    CONSTRAINT ck_investment_products_kind CHECK (kind IN ('treasury_bill', 'fixed_income')),
    CONSTRAINT ck_investment_products_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_investment_products_term CHECK (term_days BETWEEN 1 AND 3650),
    CONSTRAINT ck_investment_products_minimum CHECK (minimum_minor > 0),
    CONSTRAINT ck_investment_products_interest_paid
        CHECK (interest_paid IN ('at_maturity', 'monthly')),
    CONSTRAINT ck_investment_products_status CHECK (status IN ('draft', 'live', 'paused')),
    -- An open term has no maturity to pay interest at.
    CONSTRAINT ck_investment_products_open_term
        CHECK (term_days IS NOT NULL OR interest_paid <> 'at_maturity')
);
CREATE TABLE investment_rate_proposals (
    id push_id NOT NULL,
    product_id push_id NOT NULL,
    effective_from date NOT NULL,
    annual_rate_bps integer NOT NULL,
    tax_bps integer NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module. No foreign key: a module owns only its own tables.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    CONSTRAINT pk_investment_rate_proposals PRIMARY KEY (id),
    CONSTRAINT fk_investment_rate_proposals_product
        FOREIGN KEY (product_id) REFERENCES investment_products (id),
    CONSTRAINT ck_investment_rate_proposals_rate CHECK (annual_rate_bps BETWEEN 1 AND 5000),
    CONSTRAINT ck_investment_rate_proposals_tax CHECK (tax_bps BETWEEN 0 AND 9999),
    CONSTRAINT ck_investment_rate_proposals_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_investment_rate_proposals_status
        CHECK (status IN ('proposed', 'approved', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_investment_rate_proposals_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL) AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own proposal, and only the proposer withdraws it.
    CONSTRAINT ck_investment_rate_proposals_second_person CHECK (
        status NOT IN ('approved', 'sent_back') OR decided_by <> proposed_by),
    CONSTRAINT ck_investment_rate_proposals_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_investment_rate_proposals_note CHECK (
        status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> '')
);
-- One change waits for approval per product at a time.
CREATE UNIQUE INDEX uq_investment_rate_proposals_open
    ON investment_rate_proposals (product_id) WHERE status = 'proposed';
CREATE INDEX ix_investment_rate_proposals_product
    ON investment_rate_proposals (product_id, proposed_at DESC);
CREATE TABLE investment_rates (
    id push_id NOT NULL,
    product_id push_id NOT NULL,
    effective_from date NOT NULL,
    annual_rate_bps integer NOT NULL,
    tax_bps integer NOT NULL,
    -- The approved proposal this rate came from. NULL only for the rates seeded by a migration.
    proposal_id push_id,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_investment_rates PRIMARY KEY (id),
    CONSTRAINT fk_investment_rates_product
        FOREIGN KEY (product_id) REFERENCES investment_products (id),
    CONSTRAINT fk_investment_rates_proposal
        FOREIGN KEY (proposal_id) REFERENCES investment_rate_proposals (id),
    -- Two rates for one product never start on the same day.
    CONSTRAINT uq_investment_rates_product_date UNIQUE (product_id, effective_from),
    CONSTRAINT uq_investment_rates_proposal UNIQUE (proposal_id),
    CONSTRAINT ck_investment_rates_rate CHECK (annual_rate_bps BETWEEN 1 AND 5000),
    CONSTRAINT ck_investment_rates_tax CHECK (tax_bps BETWEEN 0 AND 9999)
)
"""

# A proposal is written once, then decided once, by the member of staff the transaction acts
# for. Its product, rate, date and reason never change.
PROPOSALS_GUARD = """
CREATE FUNCTION finvest_investment_rate_proposals_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff propose and decide rate changes' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a rate proposal starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided rate proposal never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note}'::text[])
                <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note}'::text[]) THEN
            RAISE EXCEPTION 'a rate proposal is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# A rate is added only for an approved proposal, with exactly the proposal's values. Seeded
# rates have no proposal, and only a migration can add those. Nothing changes or removes a rate.
RATES_GUARD = f"""
CREATE FUNCTION finvest_investment_rates_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        proposal investment_rate_proposals%ROWTYPE;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a rate is never changed or removed' USING ERRCODE = '42501';
        END IF;
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

RULES = [
    "REVOKE ALL ON FUNCTION finvest_investment_rate_proposals_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_rate_proposals_guard BEFORE INSERT OR UPDATE "
    "ON investment_rate_proposals FOR EACH ROW "
    "EXECUTE FUNCTION finvest_investment_rate_proposals_guard()",
    "REVOKE ALL ON FUNCTION finvest_investment_rates_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_rates_guard BEFORE INSERT OR UPDATE OR DELETE ON investment_rates "
    "FOR EACH ROW EXECUTE FUNCTION finvest_investment_rates_guard()",
    "CREATE TRIGGER investment_rates_no_truncate BEFORE TRUNCATE ON investment_rates "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_investment_rates_guard()",
    "REVOKE ALL ON investment_products, investment_rates, investment_rate_proposals FROM PUBLIC",
    # The catalogue is read by everyone and written by nobody yet.
    f"GRANT SELECT ON investment_products TO {APP_ROLE}, {WORKER_ROLE}",
    f"GRANT SELECT, INSERT ON investment_rates TO {APP_ROLE}",
    f"GRANT SELECT ON investment_rates TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) "
    f"ON investment_rate_proposals TO {APP_ROLE}",
    "ALTER TABLE investment_products ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE investment_rates ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE investment_rate_proposals ENABLE ROW LEVEL SECURITY",
    # Products and rates are the same for every client, so there is nobody to fence. A row
    # holds the tax rate, which clients never see: the API builds client responses only from
    # figures after tax. The public website reads rates with no actor at all.
    f"CREATE POLICY investment_products_read ON investment_products FOR SELECT "
    f"TO {APP_ROLE}, {WORKER_ROLE} USING (true)",
    f"CREATE POLICY investment_rates_read ON investment_rates FOR SELECT "
    f"TO {APP_ROLE}, {WORKER_ROLE} USING (true)",
    f"CREATE POLICY investment_rates_insert ON investment_rates FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
    # Proposals are staff business. Which staff may propose or approve is a permission,
    # checked in the application.
    f"CREATE POLICY investment_rate_proposals_staff ON investment_rate_proposals TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff') WITH CHECK (finvest_actor_kind() = 'staff')",
]

# The three products Finvest 1.0 sold and the rates it sold them at. A test compares these rows
# with SEED_PRODUCTS and SEED_RATES in app/modules/investments/domain.
SEED = """
INSERT INTO investment_products
    (id, code, name, kind, currency, term_days, minimum_minor, interest_paid, status, created_at, updated_at)
VALUES
    ('FvProduct1TREA91_001', 'TREA-91', '91 Day Treasury Bill', 'treasury_bill', 'UGX', 91, 100000, 'at_maturity', 'live', now(), now()),
    ('FvProduct2TREA182_01', 'TREA-182', '182 Day Treasury Bill', 'treasury_bill', 'UGX', 182, 200000, 'at_maturity', 'live', now(), now()),
    ('FvProduct3USDBOND_01', 'USD-BOND', 'USD Fixed Income', 'fixed_income', 'USD', NULL, 50000, 'monthly', 'paused', now(), now());
INSERT INTO investment_rates (id, product_id, effective_from, annual_rate_bps, tax_bps, created_at)
VALUES
    ('FvRateSeedTREA91_001', 'FvProduct1TREA91_001', DATE '2026-01-01', 1250, 1500, now()),
    ('FvRateSeedTREA182_01', 'FvProduct2TREA182_01', DATE '2026-01-01', 1375, 1500, now()),
    ('FvRateSeedUSDBOND_01', 'FvProduct3USDBOND_01', DATE '2026-01-01', 725, 500, now())
"""


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), PROPOSALS_GUARD, RATES_GUARD, *RULES, *split(SEED)]:
        op.execute(statement)


# Seeded rows can be rebuilt by the migration. Anything staff did cannot.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_rate_proposals)
        OR EXISTS (SELECT FROM investment_rates WHERE proposal_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop rates that staff proposed or approved';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE investment_rates")
    op.execute("DROP TABLE investment_rate_proposals")
    op.execute("DROP TABLE investment_products")
    op.execute("DROP FUNCTION finvest_investment_rates_guard()")
    op.execute("DROP FUNCTION finvest_investment_rate_proposals_guard()")
