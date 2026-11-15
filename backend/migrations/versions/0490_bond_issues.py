"""Bond issues and their prices, each change approved by a second person.

Revision ID: 0490_bond_issues
Revises: 0483_hosted_mobile_money

The owner decided on 2026-11-15 that clients buy a part of a specific Uganda government bond
issue, as Bank of Uganda sells them: an ISIN, a fixed coupon, coupon dates and a maturity date,
bought at the day's price. This adds the catalogue for them. Buying, coupons, early sale and
maturity come in later revisions.

* `bond_changes`: one member of staff proposes a new issue with its first price, a new price,
  a pause or an opening, with a reason. A different one approves or sends it back with a note.
  Only the proposer withdraws. A decided change never changes again, and what a change
  proposes never changes at all. One change waits per issue at a time.
* `bond_issues`: written only by an approval, by the member of staff the transaction acts for,
  with exactly the values approved. An issue's code, ISIN, currency, coupon, tax, dates and
  face amounts never change. Only its status moves, one approved change at a time.
* `bond_prices`: the price in force is the latest one. A price is written only by an approved
  new issue or price change, with that change's price, and is never changed or removed.

Issues and prices are readable on every API transaction, including one with nobody signed in,
because the public website shows them. Changes are staff business.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0490_bond_issues"
down_revision: str | None = "0483_hosted_mobile_money"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "finvest_actor_kind() = 'staff'"

TABLES = [
    """
CREATE TABLE bond_changes (
    id push_id NOT NULL,
    action varchar(8) NOT NULL,
    -- The issue changed. NULL for a new issue, which has no row until it is approved.
    issue_id push_id,
    -- The issue's version the change was made against. NULL for a new issue.
    base_version integer,
    -- A new issue, as it will be.
    code varchar(24),
    isin char(12),
    name varchar(80),
    currency char(3) COLLATE "C",
    coupon_bps integer,
    tax_bps integer,
    issue_date date,
    maturity_date date,
    minimum_face_minor bigint,
    face_step_minor bigint,
    -- A new issue's first price, or a new price. Per 100 of face value, 100.0000 = 1000000.
    price integer,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    CONSTRAINT pk_bond_changes PRIMARY KEY (id),
    CONSTRAINT ck_bond_changes_action CHECK (action IN ('create', 'price', 'pause', 'open')),
    CONSTRAINT ck_bond_changes_status
        CHECK (status IN ('proposed', 'approved', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_bond_changes_shape CHECK (
        (action = 'create') = (issue_id IS NULL)
        AND (action = 'create') = (base_version IS NULL)
        AND (action = 'create') = (code IS NOT NULL AND isin IS NOT NULL AND name IS NOT NULL
            AND currency IS NOT NULL AND coupon_bps IS NOT NULL AND tax_bps IS NOT NULL
            AND issue_date IS NOT NULL AND maturity_date IS NOT NULL
            AND minimum_face_minor IS NOT NULL AND face_step_minor IS NOT NULL)
        AND (action IN ('create', 'price')) = (price IS NOT NULL)),
    CONSTRAINT ck_bond_changes_code CHECK (code ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$'),
    CONSTRAINT ck_bond_changes_isin CHECK (isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
    CONSTRAINT ck_bond_changes_name CHECK (btrim(name) = name AND name <> ''),
    CONSTRAINT ck_bond_changes_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_bond_changes_coupon CHECK (coupon_bps BETWEEN 1 AND 5000),
    CONSTRAINT ck_bond_changes_tax CHECK (tax_bps BETWEEN 0 AND 9999),
    CONSTRAINT ck_bond_changes_dates CHECK (maturity_date > issue_date),
    CONSTRAINT ck_bond_changes_face CHECK (
        minimum_face_minor > 0 AND face_step_minor > 0
        AND minimum_face_minor % face_step_minor = 0),
    CONSTRAINT ck_bond_changes_price CHECK (price BETWEEN 500000 AND 1500000),
    CONSTRAINT ck_bond_changes_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_bond_changes_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)),
    -- A second person approves or sends back. Only the proposer withdraws.
    CONSTRAINT ck_bond_changes_second_person CHECK (
        status NOT IN ('approved', 'sent_back') OR decided_by <> proposed_by),
    CONSTRAINT ck_bond_changes_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_bond_changes_note CHECK (
        status <> 'sent_back' OR (decision_note IS NOT NULL AND btrim(decision_note) <> ''))
)
""",
    """
CREATE TABLE bond_issues (
    id push_id NOT NULL,
    code varchar(24) NOT NULL,
    isin char(12) NOT NULL,
    name varchar(80) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    coupon_bps integer NOT NULL,
    tax_bps integer NOT NULL,
    issue_date date NOT NULL,
    maturity_date date NOT NULL,
    minimum_face_minor bigint NOT NULL,
    face_step_minor bigint NOT NULL,
    status varchar(8) NOT NULL,
    version integer NOT NULL,
    -- The approved change that last wrote this row.
    change_id push_id NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_bond_issues PRIMARY KEY (id),
    CONSTRAINT uq_bond_issues_code UNIQUE (code),
    CONSTRAINT uq_bond_issues_isin UNIQUE (isin),
    CONSTRAINT fk_bond_issues_change FOREIGN KEY (change_id) REFERENCES bond_changes (id),
    CONSTRAINT ck_bond_issues_status CHECK (status IN ('draft', 'live', 'paused')),
    CONSTRAINT ck_bond_issues_version CHECK (version >= 1)
)
""",
    """
CREATE TABLE bond_prices (
    id push_id NOT NULL,
    issue_id push_id NOT NULL,
    price integer NOT NULL,
    -- The change that set it, and when it was approved: it is in force from then.
    change_id push_id NOT NULL,
    effective_at timestamptz NOT NULL,
    CONSTRAINT pk_bond_prices PRIMARY KEY (id),
    CONSTRAINT uq_bond_prices_change UNIQUE (change_id),
    CONSTRAINT fk_bond_prices_issue FOREIGN KEY (issue_id) REFERENCES bond_issues (id),
    CONSTRAINT fk_bond_prices_change FOREIGN KEY (change_id) REFERENCES bond_changes (id),
    CONSTRAINT ck_bond_prices_price CHECK (price BETWEEN 500000 AND 1500000)
)
""",
]

INDEXES = [
    # One change waits per issue, and a code or ISIN is waiting to be created once.
    "CREATE UNIQUE INDEX uq_bond_changes_waiting ON bond_changes (issue_id) "
    "WHERE status = 'proposed' AND issue_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_bond_changes_waiting_code ON bond_changes (code) "
    "WHERE status = 'proposed' AND action = 'create'",
    "CREATE UNIQUE INDEX uq_bond_changes_waiting_isin ON bond_changes (isin) "
    "WHERE status = 'proposed' AND action = 'create'",
    "CREATE INDEX ix_bond_changes_status ON bond_changes (status, proposed_at)",
    "CREATE INDEX ix_bond_prices_issue ON bond_prices (issue_id, effective_at DESC)",
]

CHANGES_GUARD = f"""
CREATE FUNCTION finvest_bond_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a bond change is never removed' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF finvest_actor_kind() IS DISTINCT FROM 'staff'
               OR NEW.proposed_by IS DISTINCT FROM finvest_actor_id()
               OR NEW.status <> 'proposed' THEN
                RAISE EXCEPTION 'a bond change is proposed by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed'
           OR finvest_actor_kind() IS DISTINCT FROM 'staff'
           OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
           OR (NEW.id, NEW.action, NEW.issue_id, NEW.base_version, NEW.code, NEW.isin, NEW.name,
               NEW.currency, NEW.coupon_bps, NEW.tax_bps, NEW.issue_date, NEW.maturity_date,
               NEW.minimum_face_minor, NEW.face_step_minor, NEW.price, NEW.reason,
               NEW.proposed_by, NEW.proposed_at)
              IS DISTINCT FROM
              (OLD.id, OLD.action, OLD.issue_id, OLD.base_version, OLD.code, OLD.isin, OLD.name,
               OLD.currency, OLD.coupon_bps, OLD.tax_bps, OLD.issue_date, OLD.maturity_date,
               OLD.minimum_face_minor, OLD.face_step_minor, OLD.price, OLD.reason,
               OLD.proposed_by, OLD.proposed_at) THEN
            RAISE EXCEPTION 'a bond change is only decided, once, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

ISSUES_GUARD = f"""
CREATE FUNCTION finvest_bond_issues_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        change bond_changes%ROWTYPE;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'a bond issue is never removed: pause it' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        SELECT * INTO change FROM bond_changes WHERE id = NEW.change_id FOR SHARE;
        IF NOT FOUND OR change.status <> 'approved'
           OR finvest_actor_kind() IS DISTINCT FROM 'staff'
           OR change.decided_by IS DISTINCT FROM finvest_actor_id() THEN
            RAISE EXCEPTION 'a bond issue changes only by an approved change'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF change.action <> 'create' OR NEW.version <> 1 OR NEW.status <> 'draft'
               OR (NEW.code, NEW.isin, NEW.name, NEW.currency, NEW.coupon_bps, NEW.tax_bps,
                   NEW.issue_date, NEW.maturity_date, NEW.minimum_face_minor,
                   NEW.face_step_minor)
                  IS DISTINCT FROM
                  (change.code, change.isin, change.name, change.currency, change.coupon_bps,
                   change.tax_bps, change.issue_date, change.maturity_date,
                   change.minimum_face_minor, change.face_step_minor) THEN
                RAISE EXCEPTION 'a new bond issue must match its approved change'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF change.action NOT IN ('pause', 'open') OR change.issue_id <> OLD.id
           OR change.base_version <> OLD.version OR NEW.version <> OLD.version + 1
           OR NEW.status <> (ARRAY['paused', 'live'])[(change.action = 'open')::int + 1]
           OR (NEW.id, NEW.code, NEW.isin, NEW.name, NEW.currency, NEW.coupon_bps, NEW.tax_bps,
               NEW.issue_date, NEW.maturity_date, NEW.minimum_face_minor, NEW.face_step_minor,
               NEW.created_at)
              IS DISTINCT FROM
              (OLD.id, OLD.code, OLD.isin, OLD.name, OLD.currency, OLD.coupon_bps, OLD.tax_bps,
               OLD.issue_date, OLD.maturity_date, OLD.minimum_face_minor, OLD.face_step_minor,
               OLD.created_at) THEN
            RAISE EXCEPTION 'a bond issue moves on one approved change at a time'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

PRICES_GUARD = f"""
CREATE FUNCTION finvest_bond_prices_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        change bond_changes%ROWTYPE;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'a bond price is never changed or removed' USING ERRCODE = '42501';
        END IF;
        IF current_user NOT IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
            RETURN NEW;
        END IF;
        SELECT * INTO change FROM bond_changes WHERE id = NEW.change_id FOR SHARE;
        IF NOT FOUND OR change.status <> 'approved' OR change.action NOT IN ('create', 'price')
           OR finvest_actor_kind() IS DISTINCT FROM 'staff'
           OR change.decided_by IS DISTINCT FROM finvest_actor_id()
           OR change.price <> NEW.price
           OR NEW.effective_at <> change.decided_at
           OR (change.action = 'price' AND change.issue_id <> NEW.issue_id)
           OR (change.action = 'create' AND NOT EXISTS (
                SELECT FROM bond_issues WHERE id = NEW.issue_id AND change_id = change.id)) THEN
            RAISE EXCEPTION 'a bond price must match its approved change' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

# Checked at commit: an approval that did not write what it approved is refused.
APPLIED = """
CREATE FUNCTION finvest_bond_changes_applied() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF (NEW.action IN ('create', 'price')
                AND NOT EXISTS (SELECT FROM bond_prices WHERE change_id = NEW.id))
           OR (NEW.action <> 'price'
                AND NOT EXISTS (SELECT FROM bond_issues WHERE change_id = NEW.id)) THEN
            RAISE EXCEPTION 'an approved bond change must be written to the catalogue'
                USING ERRCODE = '42501';
        END IF;
        RETURN NULL;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_bond_changes_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_bond_issues_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_bond_prices_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_bond_changes_applied() FROM PUBLIC",
    "CREATE TRIGGER bond_changes_guard BEFORE INSERT OR UPDATE OR DELETE ON bond_changes "
    "FOR EACH ROW EXECUTE FUNCTION finvest_bond_changes_guard()",
    "CREATE TRIGGER bond_changes_no_truncate BEFORE TRUNCATE ON bond_changes "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_bond_changes_guard()",
    "CREATE CONSTRAINT TRIGGER bond_changes_applied AFTER UPDATE ON bond_changes "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.status = 'approved') "
    "EXECUTE FUNCTION finvest_bond_changes_applied()",
    "CREATE TRIGGER bond_issues_guard BEFORE INSERT OR UPDATE OR DELETE ON bond_issues "
    "FOR EACH ROW EXECUTE FUNCTION finvest_bond_issues_guard()",
    "CREATE TRIGGER bond_issues_no_truncate BEFORE TRUNCATE ON bond_issues "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_bond_issues_guard()",
    "CREATE TRIGGER bond_prices_guard BEFORE INSERT OR UPDATE OR DELETE ON bond_prices "
    "FOR EACH ROW EXECUTE FUNCTION finvest_bond_prices_guard()",
    "CREATE TRIGGER bond_prices_no_truncate BEFORE TRUNCATE ON bond_prices "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_bond_prices_guard()",
    "REVOKE ALL ON bond_changes, bond_issues, bond_prices FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) "
    f"ON bond_changes TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, version, change_id, updated_at) "
    f"ON bond_issues TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON bond_prices TO {APP_ROLE}",
    f"GRANT SELECT ON bond_changes, bond_issues, bond_prices TO {WORKER_ROLE}",
    "ALTER TABLE bond_changes ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY bond_changes_staff ON bond_changes TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY bond_changes_worker ON bond_changes TO {WORKER_ROLE} USING (true)",
]


def upgrade() -> None:
    for statement in [
        *TABLES,
        *INDEXES,
        CHANGES_GUARD,
        ISSUES_GUARD,
        PRICES_GUARD,
        APPLIED,
        *RULES,
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM bond_changes) THEN
        RAISE EXCEPTION 'refusing to drop the record of bond issues and prices';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for statement in [
        "DROP TABLE bond_prices",
        "DROP TABLE bond_issues",
        "DROP TABLE bond_changes",
        "DROP FUNCTION finvest_bond_changes_applied()",
        "DROP FUNCTION finvest_bond_prices_guard()",
        "DROP FUNCTION finvest_bond_issues_guard()",
        "DROP FUNCTION finvest_bond_changes_guard()",
    ]:
        op.execute(statement)
