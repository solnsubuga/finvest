"""Product types staff define, each built on a way of earning the system already pays.

Revision ID: 0493_product_types
Revises: 0492_bond_coupons

The owner decided on 2026-11-15 that the firm may invent products: staff name a new product
type, such as Corporate bond or Fixed deposit, and build it on one of the return models the
system already pays correctly. A way of earning that does not exist yet still needs code.

* `product_type_changes`: one member of staff proposes a new type, an edit of its name,
  description or usual tax, or retiring or restoring it, with a reason. A different one
  approves or sends it back. Only the proposer withdraws. A decided change never changes
  again. One change waits per type at a time.
* `product_types`: written only by an approval, by the member of staff the transaction acts
  for, with exactly the values approved. A type's code and model never change. The three
  types the system started with are seeded here and are built in.
* Every product and every bond issue has a type. A product's kind is its type's model, a bond
  issue's type has the bond model, and a new product or issue takes only a type in use.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0493_product_types"
down_revision: str | None = "0492_bond_coupons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# It adds NOT NULL type columns, but the release still running names no type, and the type
# check trigger fills one in for it exactly as this release does when none is named.
zero_downtime = True

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "finvest_actor_kind() = 'staff'"
MODELS = "('treasury_bill', 'fixed_income', 'treasury_bond')"

TABLES = [
    f"""
CREATE TABLE product_type_changes (
    id push_id NOT NULL,
    action varchar(8) NOT NULL,
    -- The type changed. NULL for a new type, which has no row until it is approved.
    type_id push_id,
    base_version integer,
    -- A new type's code and model, which never change.
    code varchar(24) COLLATE "C",
    model varchar(20),
    -- A new type, or an edit: the type as it will be.
    name varchar(60),
    description varchar(300),
    default_tax_bps integer,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module. No foreign key: a module owns only its own tables.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    CONSTRAINT pk_product_type_changes PRIMARY KEY (id),
    CONSTRAINT ck_product_type_changes_action
        CHECK (action IN ('create', 'edit', 'retire', 'restore')),
    CONSTRAINT ck_product_type_changes_shape CHECK (
        (action = 'create') = (type_id IS NULL)
        AND (type_id IS NULL) = (base_version IS NULL)
        AND (action = 'create') = (code IS NOT NULL AND model IS NOT NULL)
        AND (action IN ('create', 'edit'))
            = (name IS NOT NULL AND description IS NOT NULL AND default_tax_bps IS NOT NULL)),
    CONSTRAINT ck_product_type_changes_code CHECK (code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'),
    CONSTRAINT ck_product_type_changes_model CHECK (model IN {MODELS}),
    CONSTRAINT ck_product_type_changes_name CHECK (btrim(name) = name AND name <> ''),
    CONSTRAINT ck_product_type_changes_description CHECK (btrim(description) <> ''),
    CONSTRAINT ck_product_type_changes_tax CHECK (default_tax_bps BETWEEN 0 AND 9999),
    CONSTRAINT ck_product_type_changes_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_product_type_changes_status
        CHECK (status IN ('proposed', 'approved', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_product_type_changes_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)),
    CONSTRAINT ck_product_type_changes_second_person CHECK (
        status NOT IN ('approved', 'sent_back') OR decided_by <> proposed_by),
    CONSTRAINT ck_product_type_changes_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_product_type_changes_note CHECK (
        status <> 'sent_back' OR (decision_note IS NOT NULL AND btrim(decision_note) <> ''))
)
""",
    f"""
CREATE TABLE product_types (
    id push_id NOT NULL,
    code varchar(24) COLLATE "C" NOT NULL,
    name varchar(60) NOT NULL,
    -- How it earns: the code that pays it. Never changes.
    model varchar(20) NOT NULL,
    description varchar(300) NOT NULL,
    -- The tax a new product or issue of this type starts with. Staff may enter another.
    default_tax_bps integer NOT NULL,
    status varchar(8) NOT NULL,
    -- One of the types the system started with. They have no change behind them.
    built_in boolean NOT NULL DEFAULT false,
    version integer NOT NULL,
    change_id push_id,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_product_types PRIMARY KEY (id),
    CONSTRAINT uq_product_types_code UNIQUE (code),
    CONSTRAINT uq_product_types_name UNIQUE (name),
    CONSTRAINT fk_product_types_change FOREIGN KEY (change_id)
        REFERENCES product_type_changes (id),
    CONSTRAINT ck_product_types_model CHECK (model IN {MODELS}),
    CONSTRAINT ck_product_types_status CHECK (status IN ('active', 'retired')),
    CONSTRAINT ck_product_types_tax CHECK (default_tax_bps BETWEEN 0 AND 9999),
    CONSTRAINT ck_product_types_version CHECK (version >= 1),
    CONSTRAINT ck_product_types_change CHECK (built_in OR change_id IS NOT NULL)
)
""",
]

INDEXES = [
    "CREATE UNIQUE INDEX uq_product_type_changes_waiting ON product_type_changes (type_id) "
    "WHERE status = 'proposed' AND type_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_product_type_changes_waiting_code ON product_type_changes (code) "
    "WHERE status = 'proposed' AND action = 'create'",
    "CREATE INDEX ix_product_type_changes_status ON product_type_changes (status, proposed_at)",
]

# The usual tax the system used before: 15% on the seeded bills and fixed income, Bank of
# Uganda's 20% under ten years on bonds (staff pick 10% for longer issues).
SEED = """
INSERT INTO product_types (id, code, name, model, description, default_tax_bps, status, built_in,
    version, change_id, created_at, updated_at) VALUES
('-PTYPE0000000000000B', 'TREASURY-BILL', 'Treasury bill', 'treasury_bill',
 'A term in days and a rate before tax, paid at maturity. Bought at the Bank of Uganda auction.',
 1500, 'active', true, 1, NULL, now(), now()),
('-PTYPE0000000000000F', 'FIXED-INCOME', 'Fixed income', 'fixed_income',
 'A term and a rate before tax, paid at maturity or monthly.',
 1500, 'active', true, 1, NULL, now(), now()),
('-PTYPE0000000000000T', 'TREASURY-BOND', 'Treasury bond', 'treasury_bond',
 'A Bank of Uganda issue: ISIN, coupon, dates and today''s price. Coupons every six months.',
 2000, 'active', true, 1, NULL, now(), now())
"""

LINKS = [
    # Products and their changes. A change carries the whole product, so it names its type.
    "ALTER TABLE investment_products ADD COLUMN type_id push_id",
    "ALTER TABLE investment_product_changes ADD COLUMN type_id push_id",
    "UPDATE investment_products SET type_id = CASE kind "
    "WHEN 'treasury_bill' THEN '-PTYPE0000000000000B' ELSE '-PTYPE0000000000000F' END",
    "UPDATE investment_product_changes SET type_id = CASE kind "
    "WHEN 'treasury_bill' THEN '-PTYPE0000000000000B' ELSE '-PTYPE0000000000000F' END",
    "ALTER TABLE investment_products ALTER COLUMN type_id SET NOT NULL",
    "ALTER TABLE investment_product_changes ALTER COLUMN type_id SET NOT NULL",
    "ALTER TABLE investment_products ADD CONSTRAINT fk_investment_products_type "
    "FOREIGN KEY (type_id) REFERENCES product_types (id)",
    "ALTER TABLE investment_product_changes ADD CONSTRAINT fk_investment_product_changes_type "
    "FOREIGN KEY (type_id) REFERENCES product_types (id)",
    # Bond issues. Only a new issue names a type: an issue's terms never change.
    "ALTER TABLE bond_issues ADD COLUMN type_id push_id",
    "ALTER TABLE bond_changes ADD COLUMN type_id push_id",
    "UPDATE bond_issues SET type_id = '-PTYPE0000000000000T'",
    "UPDATE bond_changes SET type_id = '-PTYPE0000000000000T' WHERE action = 'create'",
    "ALTER TABLE bond_issues ALTER COLUMN type_id SET NOT NULL",
    "ALTER TABLE bond_issues ADD CONSTRAINT fk_bond_issues_type "
    "FOREIGN KEY (type_id) REFERENCES product_types (id)",
    "ALTER TABLE bond_changes ADD CONSTRAINT fk_bond_changes_type "
    "FOREIGN KEY (type_id) REFERENCES product_types (id)",
    "ALTER TABLE bond_changes ADD CONSTRAINT ck_bond_changes_type "
    "CHECK ((action = 'create') = (type_id IS NOT NULL))",
]

CHANGES_GUARD = f"""
CREATE FUNCTION finvest_product_type_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a product type change is never removed' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF finvest_actor_kind() IS DISTINCT FROM 'staff'
               OR NEW.proposed_by IS DISTINCT FROM finvest_actor_id()
               OR NEW.status <> 'proposed' THEN
                RAISE EXCEPTION 'a product type change is proposed by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed'
           OR finvest_actor_kind() IS DISTINCT FROM 'staff'
           OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
           OR (NEW.id, NEW.action, NEW.type_id, NEW.base_version, NEW.code, NEW.model, NEW.name,
               NEW.description, NEW.default_tax_bps, NEW.reason, NEW.proposed_by,
               NEW.proposed_at)
              IS DISTINCT FROM
              (OLD.id, OLD.action, OLD.type_id, OLD.base_version, OLD.code, OLD.model, OLD.name,
               OLD.description, OLD.default_tax_bps, OLD.reason, OLD.proposed_by,
               OLD.proposed_at) THEN
            RAISE EXCEPTION 'a product type change is only decided, once, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

TYPES_GUARD = f"""
CREATE FUNCTION finvest_product_types_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        change product_type_changes%ROWTYPE;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a product type is never removed: retire it' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        SELECT * INTO change FROM product_type_changes WHERE id = NEW.change_id FOR SHARE;
        IF NOT FOUND OR change.status <> 'approved'
           OR finvest_actor_kind() IS DISTINCT FROM 'staff'
           OR change.decided_by IS DISTINCT FROM finvest_actor_id() THEN
            RAISE EXCEPTION 'a product type changes only by an approved change'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF change.action <> 'create' OR NEW.version <> 1 OR NEW.status <> 'active'
               OR NEW.built_in
               OR (NEW.code, NEW.model, NEW.name, NEW.description, NEW.default_tax_bps)
                  IS DISTINCT FROM
                  (change.code, change.model, change.name, change.description,
                   change.default_tax_bps) THEN
                RAISE EXCEPTION 'a new product type must match its approved change'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF change.action = 'create' OR change.type_id <> OLD.id
           OR change.base_version <> OLD.version OR NEW.version <> OLD.version + 1
           OR (NEW.id, NEW.code, NEW.model, NEW.built_in, NEW.created_at)
              IS DISTINCT FROM (OLD.id, OLD.code, OLD.model, OLD.built_in, OLD.created_at)
           OR (change.action = 'edit' AND (NEW.status <> OLD.status
               OR (NEW.name, NEW.description, NEW.default_tax_bps)
                  IS DISTINCT FROM (change.name, change.description, change.default_tax_bps)))
           OR (change.action IN ('retire', 'restore') AND (
               NEW.status <> (ARRAY['retired', 'active'])[(change.action = 'restore')::int + 1]
               OR (NEW.name, NEW.description, NEW.default_tax_bps)
                  IS DISTINCT FROM (OLD.name, OLD.description, OLD.default_tax_bps))) THEN
            RAISE EXCEPTION 'a product type moves on one approved change at a time'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

APPLIED = """
CREATE FUNCTION finvest_product_type_changes_applied() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF NOT EXISTS (SELECT FROM product_types WHERE change_id = NEW.id) THEN
            RAISE EXCEPTION 'an approved product type change must be written to the types'
                USING ERRCODE = '42501';
        END IF;
        RETURN NULL;
    END;
    $$
"""

# A product's kind is its type's model, and a new product, or a draft moved to another type,
# takes only a type in use. A product's type is the one its approved change names.
PRODUCT_TYPE_CHECK = f"""
CREATE FUNCTION finvest_product_type_check() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        kind_of_type varchar;
        type_status varchar;
    BEGIN
        -- The release before this one names no type: it takes the product's type, or the
        -- built-in type of the kind, as this release would with none named.
        IF NEW.type_id IS NULL AND TG_OP = 'INSERT' THEN
            IF TG_TABLE_NAME = 'investment_product_changes' THEN
                NEW.type_id := coalesce(
                    (SELECT type_id FROM investment_products WHERE id = NEW.product_id),
                    CASE NEW.kind WHEN 'treasury_bill' THEN '-PTYPE0000000000000B'
                        ELSE '-PTYPE0000000000000F' END);
            ELSIF TG_TABLE_NAME = 'investment_products' THEN
                NEW.type_id := (SELECT type_id FROM investment_product_changes
                    WHERE id = NEW.change_id);
            ELSIF TG_TABLE_NAME = 'bond_changes' AND NEW.action = 'create' THEN
                NEW.type_id := '-PTYPE0000000000000T';
            ELSIF TG_TABLE_NAME = 'bond_issues' THEN
                NEW.type_id := (SELECT type_id FROM bond_changes WHERE id = NEW.change_id);
            END IF;
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        SELECT model, status INTO kind_of_type, type_status FROM product_types
            WHERE id = NEW.type_id FOR SHARE;
        IF TG_TABLE_NAME = 'investment_product_changes' THEN
            IF TG_OP = 'INSERT' AND (kind_of_type IS DISTINCT FROM NEW.kind
                OR (type_status <> 'active' AND (NEW.action = 'create' OR NEW.type_id <> (
                    SELECT type_id FROM investment_products WHERE id = NEW.product_id)))) THEN
                RAISE EXCEPTION 'a product takes a type in use, of its own kind'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_TABLE_NAME = 'investment_products' THEN
            IF NEW.type_id IS DISTINCT FROM (
                SELECT type_id FROM investment_product_changes WHERE id = NEW.change_id) THEN
                RAISE EXCEPTION 'a product has the type its approved change names'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_TABLE_NAME = 'bond_changes' THEN
            IF TG_OP = 'INSERT' AND NEW.action = 'create'
               AND (kind_of_type IS DISTINCT FROM 'treasury_bond' OR type_status <> 'active') THEN
                RAISE EXCEPTION 'a bond issue takes a bond type in use' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        -- bond_issues
        IF TG_OP = 'INSERT' AND NEW.type_id IS DISTINCT FROM (
            SELECT type_id FROM bond_changes WHERE id = NEW.change_id) THEN
            RAISE EXCEPTION 'a bond issue has the type its approved change names'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

RULES = [
    "REVOKE ALL ON FUNCTION finvest_product_type_changes_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_product_types_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_product_type_changes_applied() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_product_type_check() FROM PUBLIC",
    "CREATE TRIGGER product_type_changes_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON product_type_changes FOR EACH ROW EXECUTE FUNCTION finvest_product_type_changes_guard()",
    "CREATE TRIGGER product_type_changes_no_truncate BEFORE TRUNCATE ON product_type_changes "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_product_type_changes_guard()",
    "CREATE CONSTRAINT TRIGGER product_type_changes_applied AFTER UPDATE "
    "ON product_type_changes DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "WHEN (NEW.status = 'approved') EXECUTE FUNCTION finvest_product_type_changes_applied()",
    "CREATE TRIGGER product_types_guard BEFORE INSERT OR UPDATE OR DELETE ON product_types "
    "FOR EACH ROW EXECUTE FUNCTION finvest_product_types_guard()",
    "CREATE TRIGGER product_types_no_truncate BEFORE TRUNCATE ON product_types "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_product_types_guard()",
    "CREATE TRIGGER investment_product_changes_type BEFORE INSERT "
    "ON investment_product_changes FOR EACH ROW EXECUTE FUNCTION finvest_product_type_check()",
    "CREATE TRIGGER investment_products_type BEFORE INSERT OR UPDATE ON investment_products "
    "FOR EACH ROW EXECUTE FUNCTION finvest_product_type_check()",
    "CREATE TRIGGER bond_changes_type BEFORE INSERT ON bond_changes "
    "FOR EACH ROW EXECUTE FUNCTION finvest_product_type_check()",
    "CREATE TRIGGER bond_issues_type BEFORE INSERT ON bond_issues "
    "FOR EACH ROW EXECUTE FUNCTION finvest_product_type_check()",
    f"GRANT UPDATE (type_id) ON investment_products TO {APP_ROLE}",
    "REVOKE ALL ON product_type_changes, product_types FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) "
    f"ON product_type_changes TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (name, description, default_tax_bps, status, version, "
    f"change_id, updated_at) ON product_types TO {APP_ROLE}",
    f"GRANT SELECT ON product_type_changes, product_types TO {WORKER_ROLE}",
    "ALTER TABLE product_type_changes ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY product_type_changes_staff ON product_type_changes TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY product_type_changes_worker ON product_type_changes TO {WORKER_ROLE} "
    "USING (true)",
]


def upgrade() -> None:
    for statement in [
        *TABLES,
        *INDEXES,
        CHANGES_GUARD,
        TYPES_GUARD,
        APPLIED,
        PRODUCT_TYPE_CHECK,
        SEED,
        *LINKS,
        *RULES,
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM product_type_changes) THEN
        RAISE EXCEPTION 'refusing to drop the product types staff defined';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in [
        f"REVOKE UPDATE (type_id) ON investment_products FROM {APP_ROLE}",
        "DROP TRIGGER bond_issues_type ON bond_issues",
        "DROP TRIGGER bond_changes_type ON bond_changes",
        "DROP TRIGGER investment_products_type ON investment_products",
        "DROP TRIGGER investment_product_changes_type ON investment_product_changes",
        "ALTER TABLE bond_changes DROP COLUMN type_id",
        "ALTER TABLE bond_issues DROP COLUMN type_id",
        "ALTER TABLE investment_product_changes DROP COLUMN type_id",
        "ALTER TABLE investment_products DROP COLUMN type_id",
        "DROP TABLE product_types",
        "DROP TABLE product_type_changes",
        "DROP FUNCTION finvest_product_type_check()",
        "DROP FUNCTION finvest_product_type_changes_applied()",
        "DROP FUNCTION finvest_product_types_guard()",
        "DROP FUNCTION finvest_product_type_changes_guard()",
    ]:
        op.execute(statement)
