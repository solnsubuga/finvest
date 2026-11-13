-- Checks a restored Finvest database before anyone trusts it. Read only: it changes nothing.
--
--   psql "$RESTORED_ADMIN_URL" -v ON_ERROR_STOP=1 -f backend/db/restore-checks.sql
--
-- Run it as the database administrator or finvest_migrator, which can see every row. It stops
-- with an error if the ledger does not hold together, and otherwise prints what the person
-- running the drill compares with production: the migration head, the last posting and the
-- number of rows in the tables that matter. It prints no personal data and no amounts.
-- docs/backup-and-restore.md describes the drill.

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

DO $$
DECLARE
    unbalanced bigint;
    short bigint;
    drifted bigint;
    missing bigint;
BEGIN
    -- Every journal entry's lines sum to zero.
    SELECT count(*) INTO unbalanced FROM (
        SELECT entry_id FROM public.ledger_lines GROUP BY entry_id HAVING sum(amount_minor) <> 0
    ) AS e;
    IF unbalanced > 0 THEN
        RAISE EXCEPTION 'restore check failed: % journal entries do not sum to zero', unbalanced;
    END IF;

    -- Every journal entry has at least two lines.
    SELECT count(*) INTO short FROM public.ledger_journal_entries AS e
    WHERE (SELECT count(*) FROM public.ledger_lines AS l WHERE l.entry_id = e.id) < 2;
    IF short > 0 THEN
        RAISE EXCEPTION 'restore check failed: % journal entries have fewer than two lines', short;
    END IF;

    -- Every account has a balance row.
    SELECT count(*) INTO missing FROM public.ledger_accounts AS a
    WHERE NOT EXISTS (SELECT FROM public.ledger_balances AS b WHERE b.account_id = a.id);
    IF missing > 0 THEN
        RAISE EXCEPTION 'restore check failed: % ledger accounts have no balance row', missing;
    END IF;

    -- Every stored balance equals the sum of its account's lines, signed the way the posting
    -- trigger signs them: debits raise asset and expense accounts, credits raise the others.
    SELECT count(*) INTO drifted FROM public.ledger_accounts AS a
    JOIN public.ledger_balances AS b ON b.account_id = a.id
    WHERE b.balance_minor <> coalesce((
        SELECT sum(CASE WHEN a.kind IN ('asset', 'expense') THEN l.amount_minor
                        ELSE -l.amount_minor END)
        FROM public.ledger_lines AS l WHERE l.account_id = a.id
    ), 0);
    IF drifted > 0 THEN
        RAISE EXCEPTION 'restore check failed: % ledger balances differ from their lines', drifted;
    END IF;
END
$$;

\echo 'Ledger: every entry balances, and every stored balance matches its lines.'

SELECT version_num AS migration_head FROM public.alembic_version;

SELECT max(posted_at) AS last_posting, max(effective_date) AS last_effective_date
FROM public.ledger_journal_entries;

SELECT 'identity_clients' AS table_name, count(*) AS row_count FROM public.identity_clients
UNION ALL SELECT 'identity_staff', count(*) FROM public.identity_staff
UNION ALL SELECT 'ledger_accounts', count(*) FROM public.ledger_accounts
UNION ALL SELECT 'ledger_journal_entries', count(*) FROM public.ledger_journal_entries
UNION ALL SELECT 'ledger_lines', count(*) FROM public.ledger_lines
UNION ALL SELECT 'audit_events', count(*) FROM public.audit_events
UNION ALL SELECT 'investments', count(*) FROM public.investments
UNION ALL SELECT 'payment_deposits', count(*) FROM public.payment_deposits
UNION ALL SELECT 'payouts', count(*) FROM public.payouts
UNION ALL SELECT 'kyc_checks', count(*) FROM public.kyc_checks
UNION ALL SELECT 'jobs', count(*) FROM public.jobs
ORDER BY 1;

COMMIT;
