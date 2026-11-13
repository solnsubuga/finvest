"""Add the double-entry ledger: accounts, journal entries, lines and cached balances.

Revision ID: 0010_ledger
Revises: 0001_foundation

The rules in docs/rules.md are enforced here, not only in application code:

* Insert only. The service roles hold SELECT and INSERT on accounts, entries and lines, and
  SELECT on balances. Triggers refuse UPDATE, DELETE and TRUNCATE for every role, the table
  owner included.
* Every entry balances. A deferred constraint trigger checks at commit that an entry has at
  least two lines and that they sum to zero.
* History is closed. Lines can be added only by the transaction that created their entry.
* Balances never go negative. Each line updates its account's cached balance in the same
  statement, and a CHECK constraint refuses a balance below zero.
* A reversal is exact. An entry that reverses another must mirror it line for line, an entry
  is reversed at most once, and a reversal is not itself reversed.
* Clients are fenced. Row policies show a client transaction only that client's accounts,
  balances, lines and entries. Staff transactions and the worker see the whole ledger.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_ledger"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

CODE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
IDENTIFIER = "^[A-Za-z0-9._:-]{1,64}$"

TABLES = f"""
CREATE TABLE ledger_accounts (
    id push_id NOT NULL,
    code varchar(100) NOT NULL,
    kind varchar(16) NOT NULL,
    currency varchar(3) NOT NULL,
    name varchar(120) NOT NULL,
    owner_client_id push_id,
    reference varchar(64),
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_ledger_accounts PRIMARY KEY (id),
    CONSTRAINT uq_ledger_accounts_id_currency UNIQUE (id, currency),
    CONSTRAINT uq_ledger_accounts_identity
        UNIQUE NULLS NOT DISTINCT (code, currency, owner_client_id, reference),
    CONSTRAINT ck_ledger_accounts_code CHECK (code ~ '{CODE}'),
    CONSTRAINT ck_ledger_accounts_kind
        CHECK (kind IN ('asset', 'liability', 'equity', 'income', 'expense')),
    CONSTRAINT ck_ledger_accounts_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_ledger_accounts_name CHECK (length(btrim(name)) > 0),
    CONSTRAINT ck_ledger_accounts_reference CHECK (reference ~ '{IDENTIFIER}')
);
CREATE INDEX ix_ledger_accounts_owner_client_id
    ON ledger_accounts (owner_client_id) WHERE owner_client_id IS NOT NULL;

CREATE TABLE ledger_balances (
    account_id push_id NOT NULL,
    balance_minor bigint NOT NULL DEFAULT 0,
    CONSTRAINT pk_ledger_balances PRIMARY KEY (account_id),
    CONSTRAINT fk_ledger_balances_account_id_ledger_accounts
        FOREIGN KEY (account_id) REFERENCES ledger_accounts (id),
    CONSTRAINT ck_ledger_balances_non_negative CHECK (balance_minor >= 0)
);

CREATE TABLE ledger_journal_entries (
    id push_id NOT NULL,
    entry_type varchar(100) NOT NULL,
    currency varchar(3) NOT NULL,
    effective_date date NOT NULL,
    posted_at timestamptz NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_digest varchar(64) NOT NULL,
    source_type varchar(100),
    source_id varchar(64),
    reverses_entry_id push_id,
    correlation_id varchar(64),
    created_xact xid8 NOT NULL DEFAULT pg_current_xact_id(),
    CONSTRAINT pk_ledger_journal_entries PRIMARY KEY (id),
    CONSTRAINT uq_ledger_journal_entries_id_currency UNIQUE (id, currency),
    CONSTRAINT uq_ledger_journal_entries_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_ledger_journal_entries_reverses_entry_id UNIQUE (reverses_entry_id),
    CONSTRAINT fk_ledger_journal_entries_reverses_entry_id
        FOREIGN KEY (reverses_entry_id) REFERENCES ledger_journal_entries (id),
    CONSTRAINT ck_ledger_journal_entries_entry_type CHECK (entry_type ~ '{CODE}'),
    CONSTRAINT ck_ledger_journal_entries_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_ledger_journal_entries_idempotency_key
        CHECK (idempotency_key ~ '^[A-Za-z0-9._:-]{{8,128}}$'),
    CONSTRAINT ck_ledger_journal_entries_request_digest CHECK (request_digest ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT ck_ledger_journal_entries_source CHECK (
        (source_type IS NULL AND source_id IS NULL)
        OR (source_type ~ '{CODE}' AND source_id ~ '{IDENTIFIER}')
    ),
    CONSTRAINT ck_ledger_journal_entries_not_self_reversing CHECK (reverses_entry_id <> id),
    CONSTRAINT ck_ledger_journal_entries_correlation_id CHECK (correlation_id ~ '{IDENTIFIER}')
);
CREATE INDEX ix_ledger_journal_entries_source
    ON ledger_journal_entries (source_type, source_id) WHERE source_type IS NOT NULL;
CREATE INDEX ix_ledger_journal_entries_effective_date
    ON ledger_journal_entries (effective_date, id);

CREATE TABLE ledger_lines (
    entry_id push_id NOT NULL,
    line_no integer NOT NULL,
    account_id push_id NOT NULL,
    currency varchar(3) NOT NULL,
    amount_minor bigint NOT NULL,
    CONSTRAINT pk_ledger_lines PRIMARY KEY (entry_id, line_no),
    CONSTRAINT uq_ledger_lines_entry_id_account_id UNIQUE (entry_id, account_id),
    CONSTRAINT fk_ledger_lines_entry FOREIGN KEY (entry_id, currency)
        REFERENCES ledger_journal_entries (id, currency),
    CONSTRAINT fk_ledger_lines_account FOREIGN KEY (account_id, currency)
        REFERENCES ledger_accounts (id, currency),
    CONSTRAINT ck_ledger_lines_line_no CHECK (line_no > 0),
    CONSTRAINT ck_ledger_lines_amount_minor CHECK (amount_minor <> 0)
);
CREATE INDEX ix_ledger_lines_account_id ON ledger_lines (account_id, entry_id)
"""

# Trigger functions run as the table owner (SECURITY DEFINER), because the service roles cannot
# update balances and a client transaction cannot see every line of an entry. Each pins its
# search path and names tables with their schema.
FUNCTIONS = [
    """
CREATE FUNCTION finvest_ledger_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'ledger table % is insert-only', TG_TABLE_NAME USING ERRCODE = '42501';
    END;
    $$
""",
    """
CREATE FUNCTION finvest_ledger_open_balance() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        INSERT INTO public.ledger_balances (account_id, balance_minor) VALUES (NEW.id, 0);
        RETURN NULL;
    END;
    $$
""",
    """
CREATE FUNCTION finvest_ledger_stamp_entry() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        -- Never trust a supplied value: lines are tied to the transaction that made the entry.
        NEW.created_xact := pg_current_xact_id();
        RETURN NEW;
    END;
    $$
""",
    """
CREATE FUNCTION finvest_ledger_apply_line() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        entry_xact xid8;
    BEGIN
        SELECT created_xact INTO entry_xact
        FROM public.ledger_journal_entries WHERE id = NEW.entry_id;
        IF entry_xact IS DISTINCT FROM pg_current_xact_id() THEN
            RAISE EXCEPTION 'lines can be added only in the transaction that created the entry'
                USING ERRCODE = '42501';
        END IF;
        -- Debits raise asset and expense balances; credits raise the others. The row lock taken
        -- here serialises postings to one account, and the CHECK on balance_minor refuses a
        -- balance below zero.
        UPDATE public.ledger_balances AS balance
        SET balance_minor = balance.balance_minor
            + CASE WHEN account.kind IN ('asset', 'expense')
                   THEN NEW.amount_minor ELSE -NEW.amount_minor END
        FROM public.ledger_accounts AS account
        WHERE account.id = NEW.account_id AND balance.account_id = account.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'ledger account has no balance row' USING ERRCODE = '23503';
        END IF;
        RETURN NULL;
    END;
    $$
""",
    """
CREATE FUNCTION finvest_ledger_check_entry() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        line_count bigint;
        line_total numeric;
    BEGIN
        SELECT count(*), coalesce(sum(amount_minor), 0) INTO line_count, line_total
        FROM public.ledger_lines WHERE entry_id = NEW.id;
        IF line_count < 2 THEN
            RAISE EXCEPTION 'journal entry needs at least two lines'
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_entry_has_lines';
        END IF;
        IF line_total <> 0 THEN
            RAISE EXCEPTION 'journal entry lines must sum to zero'
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_entry_balanced';
        END IF;
        IF NEW.reverses_entry_id IS NOT NULL THEN
            IF EXISTS (
                SELECT FROM public.ledger_journal_entries
                WHERE id = NEW.reverses_entry_id AND reverses_entry_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'a reversal cannot itself be reversed'
                    USING ERRCODE = '23514', CONSTRAINT = 'ledger_reversal_of_reversal';
            END IF;
            IF EXISTS (
                (SELECT account_id, amount_minor FROM public.ledger_lines WHERE entry_id = NEW.id
                 EXCEPT
                 SELECT account_id, -amount_minor FROM public.ledger_lines
                 WHERE entry_id = NEW.reverses_entry_id)
                UNION ALL
                (SELECT account_id, -amount_minor FROM public.ledger_lines
                 WHERE entry_id = NEW.reverses_entry_id
                 EXCEPT
                 SELECT account_id, amount_minor FROM public.ledger_lines WHERE entry_id = NEW.id)
            ) THEN
                RAISE EXCEPTION 'a reversal must mirror the entry it reverses'
                    USING ERRCODE = '23514', CONSTRAINT = 'ledger_reversal_mirrors';
            END IF;
        END IF;
        RETURN NULL;
    END;
    $$
""",
]

TRIGGERS = [
    *(
        f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC"
        for name in (
            "finvest_ledger_immutable",
            "finvest_ledger_open_balance",
            "finvest_ledger_stamp_entry",
            "finvest_ledger_apply_line",
            "finvest_ledger_check_entry",
        )
    ),
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_immutable()"
        for table in ("ledger_accounts", "ledger_journal_entries", "ledger_lines")
    ),
    *(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_ledger_immutable()"
        for table in (
            "ledger_accounts",
            "ledger_balances",
            "ledger_journal_entries",
            "ledger_lines",
        )
    ),
    # A balance row is never deleted either; it changes only through finvest_ledger_apply_line.
    "CREATE TRIGGER ledger_balances_no_delete BEFORE DELETE ON ledger_balances "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_immutable()",
    "CREATE TRIGGER ledger_accounts_open_balance AFTER INSERT ON ledger_accounts "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_open_balance()",
    "CREATE TRIGGER ledger_journal_entries_stamp BEFORE INSERT ON ledger_journal_entries "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_stamp_entry()",
    "CREATE TRIGGER ledger_lines_apply AFTER INSERT ON ledger_lines "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_apply_line()",
    "CREATE CONSTRAINT TRIGGER ledger_journal_entries_check AFTER INSERT ON ledger_journal_entries "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finvest_ledger_check_entry()",
]

IS_STAFF = "finvest_actor_kind() = 'staff'"
IS_CLIENT = "finvest_actor_kind() = 'client'"
OWN_ACCOUNTS = "SELECT id FROM ledger_accounts WHERE owner_client_id = finvest_actor_id()"


def policies() -> list[str]:
    statements: list[str] = []
    for table in ("ledger_accounts", "ledger_balances", "ledger_journal_entries", "ledger_lines"):
        privileges = "SELECT" if table == "ledger_balances" else "SELECT, INSERT"
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {table} FROM PUBLIC",
            f"GRANT {privileges} ON {table} TO {APP_ROLE}, {WORKER_ROLE}",
            # Workers act for Finvest itself and see the whole ledger.
            f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER_ROLE} USING (true)",
        ]
        if table != "ledger_balances":
            statements.append(
                f"CREATE POLICY {table}_worker_insert ON {table} FOR INSERT TO {WORKER_ROLE} "
                "WITH CHECK (true)"
            )
    # API reads: a staff transaction sees everything (permissions are checked in the
    # application); a client transaction sees only what belongs to that client; any other
    # transaction sees nothing. The API role is never trusted as the system actor.
    own = {
        "ledger_accounts": "owner_client_id = finvest_actor_id()",
        "ledger_balances": f"account_id IN ({OWN_ACCOUNTS})",
        "ledger_lines": f"account_id IN ({OWN_ACCOUNTS})",
        "ledger_journal_entries": (
            "EXISTS (SELECT FROM ledger_lines AS line "
            "WHERE line.entry_id = ledger_journal_entries.id)"
        ),
    }
    for table, condition in own.items():
        statements.append(
            f"CREATE POLICY {table}_app_read ON {table} FOR SELECT TO {APP_ROLE} "
            f"USING ({IS_STAFF} OR ({IS_CLIENT} AND {condition}))"
        )
    statements += [
        # A client transaction can open accounts only for that client.
        f"CREATE POLICY ledger_accounts_app_insert ON ledger_accounts FOR INSERT TO {APP_ROLE} "
        f"WITH CHECK ({IS_STAFF} OR ({IS_CLIENT} AND owner_client_id = finvest_actor_id()))",
        # Posting through the API needs a signed-in person. Which entries a command may post is
        # application logic.
        f"CREATE POLICY ledger_journal_entries_app_insert ON ledger_journal_entries FOR INSERT "
        f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() IN ('client', 'staff'))",
        f"CREATE POLICY ledger_lines_app_insert ON ledger_lines FOR INSERT TO {APP_ROLE} "
        "WITH CHECK (finvest_actor_kind() IN ('client', 'staff'))",
    ]
    return statements


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *FUNCTIONS, *TRIGGERS, *policies()]:
        op.execute(statement)


def downgrade() -> None:
    # Read policies name other ledger tables, so they go before the tables they name.
    for table in ("ledger_journal_entries", "ledger_lines", "ledger_balances"):
        op.execute(f"DROP POLICY {table}_app_read ON {table}")
    for table in ("ledger_lines", "ledger_journal_entries", "ledger_balances", "ledger_accounts"):
        op.execute(f"DROP TABLE {table}")
    for function in (
        "finvest_ledger_check_entry",
        "finvest_ledger_apply_line",
        "finvest_ledger_stamp_entry",
        "finvest_ledger_open_balance",
        "finvest_ledger_immutable",
    ):
        op.execute(f"DROP FUNCTION {function}()")
