"""Add the registry of issued account numbers.

Revision ID: 0011_account_numbers
Revises: 0010_ledger

A number enters this table when it is issued and never leaves, so it can never be issued twice,
even after the account it named is closed. The table is insert-only for every role, and the
database checks the format and the Luhn check digit itself. See docs/account-numbers.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_account_numbers"
down_revision: str | None = "0010_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

STATEMENTS = [
    # Position 0 is the check digit; every second digit to its left is doubled.
    """
CREATE FUNCTION finvest_luhn_valid(value text) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = pg_catalog, pg_temp
    AS $$
    SELECT CASE WHEN value ~ '^[0-9]+$' THEN (
        SELECT sum(
            CASE WHEN (length(value) - position) % 2 = 1
                 THEN digit * 2 - CASE WHEN digit * 2 > 9 THEN 9 ELSE 0 END
                 ELSE digit END
        ) % 10 = 0
        FROM (
            SELECT position, substr(value, position, 1)::integer AS digit
            FROM generate_series(1, length(value)) AS position
        ) AS digits
    ) ELSE false END
    $$
""",
    """
CREATE TABLE account_numbers (
    number varchar(10) COLLATE "C" NOT NULL,
    issued_at timestamptz NOT NULL,
    CONSTRAINT pk_account_numbers PRIMARY KEY (number),
    CONSTRAINT ck_account_numbers_format CHECK (number ~ '^(10|20|30|40)[0-9]{8}$'),
    CONSTRAINT ck_account_numbers_check_digit CHECK (finvest_luhn_valid(number))
)
""",
    """
CREATE FUNCTION finvest_account_numbers_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'issued account numbers are never changed or removed'
            USING ERRCODE = '42501';
    END;
    $$
""",
    # The CHECK constraint calls this as the inserting role.
    "REVOKE ALL ON FUNCTION finvest_luhn_valid(text) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION finvest_luhn_valid(text) TO {APP_ROLE}, {WORKER_ROLE}",
    "REVOKE ALL ON FUNCTION finvest_account_numbers_immutable() FROM PUBLIC",
    "CREATE TRIGGER account_numbers_immutable BEFORE UPDATE OR DELETE ON account_numbers "
    "FOR EACH ROW EXECUTE FUNCTION finvest_account_numbers_immutable()",
    "CREATE TRIGGER account_numbers_no_truncate BEFORE TRUNCATE ON account_numbers "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_account_numbers_immutable()",
    "REVOKE ALL ON account_numbers FROM PUBLIC",
    f"GRANT SELECT, INSERT ON account_numbers TO {APP_ROLE}, {WORKER_ROLE}",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE account_numbers")
    op.execute("DROP FUNCTION finvest_account_numbers_immutable()")
    op.execute("DROP FUNCTION finvest_luhn_valid(text)")
