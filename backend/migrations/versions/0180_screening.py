"""Sanctions and politically exposed person screening, and compliance cases.

Revision ID: 0180_screening
Revises: 0250_finance_journal_exports

* `screening_runs` holds every screen of a client against the provider's lists: why it was
  asked for, and what came back. A run starts pending and is finished once, by the worker.
  Anyone signed in may ask for one (a client handing in their identity check, a member of staff,
  the worker's sweep), but only for themselves or as staff, and only as pending.
* `screening_matches` holds the listed entities a finished run found. The worker writes them,
  and nothing changes them.
* `compliance_cases` holds what staff must decide: a screen that found someone new, or (from
  migration 0182) a transaction monitoring rule that fired. Only the worker opens one.
* `compliance_case_proposals` closes a case with two people: one member of staff proposes the
  outcome, a different one approves it or sends it back, and only the proposer withdraws it.
  A case is closed only by a proposal approved in the same transaction, with its outcome.

Nothing here is ever deleted. A screen, a case and a decision are part of the compliance record.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0180_screening"
down_revision: str | None = "0250_finance_journal_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = "finvest_app"
WORKER = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"

REASONS = "'onboarding', 'rescreen', 'payout', 'staff'"
FINISHED = "'clear', 'possible_match', 'unavailable', 'not_run', 'no_subject'"
CATEGORIES = "'sanctions', 'pep', 'adverse_media', 'other'"
SCREENING_OUTCOMES = "'not_a_match', 'pep_accepted', 'sanctioned'"
MONITORING_OUTCOMES = "'no_concern', 'report_to_fia'"

TABLES = f"""
CREATE TABLE screening_runs (
    id push_id NOT NULL,
    -- A client in the identity module: no foreign key, a module owns only its tables.
    client_id push_id NOT NULL,
    reason varchar(20) NOT NULL,
    -- The member of staff who asked, for a screen asked for by hand.
    requested_by push_id,
    requested_at timestamptz NOT NULL,
    -- The same key asks once: a retried hand-in or a repeated sweep adds no second screen.
    idempotency_key varchar(200) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'pending',
    finished_at timestamptz,
    provider varchar(40),
    provider_reference varchar(200),
    -- Why the provider would not screen the client, or why we gave up waiting.
    failure_code varchar(60),
    match_count integer,
    -- Matches not already decided for this client. Only these open a case.
    new_match_count integer,
    CONSTRAINT pk_screening_runs PRIMARY KEY (id),
    CONSTRAINT uq_screening_runs_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT ck_screening_runs_reason CHECK (reason IN ({REASONS})),
    CONSTRAINT ck_screening_runs_requested_by CHECK ((reason = 'staff') = (requested_by IS NOT NULL)),
    CONSTRAINT ck_screening_runs_status CHECK (status IN ('pending', {FINISHED})),
    CONSTRAINT ck_screening_runs_finished CHECK ((status = 'pending') = (finished_at IS NULL)),
    CONSTRAINT ck_screening_runs_counts CHECK (
        (status IN ('clear', 'possible_match')) = (match_count IS NOT NULL)
        AND (match_count IS NULL) = (new_match_count IS NULL)
        AND (status <> 'clear' OR match_count = 0)
        AND (status <> 'possible_match' OR match_count > 0)
        AND new_match_count BETWEEN 0 AND match_count),
    CONSTRAINT ck_screening_runs_failure CHECK ((status = 'unavailable') = (failure_code IS NOT NULL))
);
CREATE INDEX ix_screening_runs_client ON screening_runs (client_id, requested_at DESC, id DESC);
CREATE INDEX ix_screening_runs_pending ON screening_runs (requested_at) WHERE status = 'pending';
CREATE INDEX ix_screening_runs_finished ON screening_runs (finished_at DESC) WHERE status <> 'pending';

CREATE TABLE screening_matches (
    run_id push_id NOT NULL,
    position smallint NOT NULL,
    entity_id varchar(200) NOT NULL,
    -- The listed entity's name, as the provider gives it. Not the client's.
    name varchar(300) NOT NULL,
    category varchar(20) NOT NULL,
    strength smallint NOT NULL,
    sources text[] NOT NULL DEFAULT '{{}}',
    -- Already ruled out or accepted for this client on an earlier case.
    known boolean NOT NULL,
    CONSTRAINT pk_screening_matches PRIMARY KEY (run_id, position),
    CONSTRAINT fk_screening_matches_run_id_screening_runs
        FOREIGN KEY (run_id) REFERENCES screening_runs (id),
    CONSTRAINT uq_screening_matches_entity UNIQUE (run_id, entity_id),
    CONSTRAINT ck_screening_matches_category CHECK (category IN ({CATEGORIES})),
    CONSTRAINT ck_screening_matches_strength CHECK (strength BETWEEN 0 AND 100),
    CONSTRAINT ck_screening_matches_position CHECK (position >= 0)
);

CREATE TABLE compliance_cases (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    source varchar(20) NOT NULL,
    -- What raised it: `possible_match` for screening, a rule's code for monitoring.
    code varchar(60) NOT NULL,
    -- The screen whose new matches the case is about.
    run_id push_id,
    -- For monitoring: what makes a rule's alert one alert, so a retry raises it once.
    dedupe_key varchar(200),
    -- For monitoring: the transactions and figures the rule saw, as IDs and numbers.
    detail jsonb NOT NULL DEFAULT '{{}}',
    opened_at timestamptz NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'open',
    outcome varchar(20),
    closed_at timestamptz,
    closed_by_proposal push_id,
    CONSTRAINT pk_compliance_cases PRIMARY KEY (id),
    CONSTRAINT fk_compliance_cases_run_id_screening_runs
        FOREIGN KEY (run_id) REFERENCES screening_runs (id),
    CONSTRAINT uq_compliance_cases_run_id UNIQUE (run_id),
    CONSTRAINT uq_compliance_cases_dedupe UNIQUE (code, dedupe_key),
    CONSTRAINT ck_compliance_cases_source CHECK (
        (source = 'screening' AND run_id IS NOT NULL AND code = 'possible_match'
            AND dedupe_key IS NULL)
        OR (source = 'monitoring' AND run_id IS NULL AND dedupe_key IS NOT NULL)),
    CONSTRAINT ck_compliance_cases_status CHECK (status IN ('open', 'closed')),
    CONSTRAINT ck_compliance_cases_closed CHECK (
        (status = 'closed') = (outcome IS NOT NULL)
        AND (status = 'closed') = (closed_at IS NOT NULL)
        AND (status = 'closed') = (closed_by_proposal IS NOT NULL)),
    CONSTRAINT ck_compliance_cases_outcome CHECK (
        outcome IS NULL
        OR (source = 'screening' AND outcome IN ({SCREENING_OUTCOMES}))
        OR (source = 'monitoring' AND outcome IN ({MONITORING_OUTCOMES})))
);
CREATE INDEX ix_compliance_cases_open ON compliance_cases (opened_at, id) WHERE status = 'open';
CREATE INDEX ix_compliance_cases_client ON compliance_cases (client_id, opened_at DESC);

CREATE TABLE compliance_case_proposals (
    id push_id NOT NULL,
    case_id push_id NOT NULL,
    outcome varchar(20) NOT NULL,
    reason varchar(1000) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(1000),
    -- The transaction that approved it. The case guard accepts a close only there.
    decided_in xid8,
    CONSTRAINT pk_compliance_case_proposals PRIMARY KEY (id),
    CONSTRAINT fk_compliance_case_proposals_case_id_compliance_cases
        FOREIGN KEY (case_id) REFERENCES compliance_cases (id),
    CONSTRAINT fk_compliance_case_proposals_proposed_by_identity_staff
        FOREIGN KEY (proposed_by) REFERENCES identity_staff (id),
    CONSTRAINT fk_compliance_case_proposals_decided_by_identity_staff
        FOREIGN KEY (decided_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_compliance_case_proposals_outcome
        CHECK (outcome IN ({SCREENING_OUTCOMES}, {MONITORING_OUTCOMES})),
    CONSTRAINT ck_compliance_case_proposals_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_compliance_case_proposals_status
        CHECK (status IN ('proposed', 'approved', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_compliance_case_proposals_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (status = 'approved') = (decided_in IS NOT NULL)),
    CONSTRAINT ck_compliance_case_proposals_note
        CHECK (status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    CONSTRAINT ck_compliance_case_proposals_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'sent_back') AND decided_by <> proposed_by))
);
-- One open proposal per case.
CREATE UNIQUE INDEX uq_compliance_case_proposals_open
    ON compliance_case_proposals (case_id) WHERE status = 'proposed';
CREATE INDEX ix_compliance_case_proposals_waiting
    ON compliance_case_proposals (proposed_at) WHERE status = 'proposed'
"""

RUNS_GUARD = """
CREATE FUNCTION finvest_screening_runs_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'pending' OR NEW.finished_at IS NOT NULL
                OR NEW.match_count IS NOT NULL OR NEW.provider IS NOT NULL
                OR (NEW.requested_by IS NOT NULL AND (
                    public.finvest_actor_kind() IS DISTINCT FROM 'staff'
                    OR NEW.requested_by <> public.finvest_actor_id())) THEN
                RAISE EXCEPTION 'a screen starts pending, asked for by whoever is signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the worker finishes a screen' USING ERRCODE = '42501';
        END IF;
        IF OLD.status <> 'pending' OR NEW.status = 'pending'
            OR (to_jsonb(NEW) - ARRAY['status', 'finished_at', 'provider', 'provider_reference',
                                      'failure_code', 'match_count', 'new_match_count'])
               <> (to_jsonb(OLD) - ARRAY['status', 'finished_at', 'provider',
                                         'provider_reference', 'failure_code', 'match_count',
                                         'new_match_count']) THEN
            RAISE EXCEPTION 'a screen is finished once and never changes'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

MATCHES_GUARD = """
CREATE FUNCTION finvest_screening_matches_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'system' THEN
            RAISE EXCEPTION 'only the worker records what a screen found'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# Runs as the table owner so it can read the proposal whatever the caller's row policies show.
CASES_GUARD = """
CREATE FUNCTION finvest_compliance_cases_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF public.finvest_actor_kind() IS DISTINCT FROM 'system' OR NEW.status <> 'open'
                OR NEW.closed_by_proposal IS NOT NULL THEN
                RAISE EXCEPTION 'only the worker opens a case, and it starts open'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.source = 'screening' AND NOT EXISTS (
                SELECT FROM public.screening_runs AS run
                 WHERE run.id = NEW.run_id AND run.client_id = NEW.client_id
                   AND run.status = 'possible_match' AND run.new_match_count > 0) THEN
                RAISE EXCEPTION 'a screening case is opened for a screen with new matches'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'open' OR NEW.status <> 'closed'
            OR NOT EXISTS (
                SELECT FROM public.compliance_case_proposals AS p
                 WHERE p.id = NEW.closed_by_proposal AND p.case_id = NEW.id
                   AND p.status = 'approved' AND p.outcome = NEW.outcome
                   AND p.decided_in = pg_current_xact_id()
                   AND p.decided_by = public.finvest_actor_id())
            OR (to_jsonb(NEW) - ARRAY['status', 'outcome', 'closed_at', 'closed_by_proposal'])
               <> (to_jsonb(OLD) - ARRAY['status', 'outcome', 'closed_at', 'closed_by_proposal'])
        THEN
            RAISE EXCEPTION 'a case is closed once, by the proposal approved with it'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

PROPOSALS_GUARD = """
CREATE FUNCTION finvest_compliance_case_proposals_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        open_case public.compliance_cases%ROWTYPE;
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' OR NOT EXISTS (
            SELECT FROM public.identity_staff
             WHERE id = public.finvest_actor_id() AND status = 'active') THEN
            RAISE EXCEPTION 'only active staff decide a case' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT * INTO open_case FROM public.compliance_cases WHERE id = NEW.case_id;
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id()
                OR open_case.id IS NULL OR open_case.status <> 'open'
                OR (open_case.source = 'screening'
                    AND NEW.outcome NOT IN ('not_a_match', 'pep_accepted', 'sanctioned'))
                OR (open_case.source = 'monitoring'
                    AND NEW.outcome NOT IN ('no_concern', 'report_to_fia')) THEN
                RAISE EXCEPTION 'a proposal is made by the person signed in, for an open case, '
                    'with an outcome that fits it' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided proposal never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (NEW.status = 'approved') <> (NEW.decided_in = pg_current_xact_id())
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_note',
                                      'decided_in'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_note',
                                         'decided_in']) THEN
            RAISE EXCEPTION 'a proposal is decided once, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

IMMUTABLE = """
CREATE FUNCTION finvest_screening_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'the compliance record is never deleted' USING ERRCODE = '42501';
    END;
    $$
"""

GUARDED = (
    "finvest_screening_runs_guard",
    "finvest_screening_matches_guard",
    "finvest_compliance_cases_guard",
    "finvest_compliance_case_proposals_guard",
    "finvest_screening_immutable",
)
TABLE_NAMES = (
    "screening_runs",
    "screening_matches",
    "compliance_cases",
    "compliance_case_proposals",
)

RULES = [
    *(f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC" for name in GUARDED),
    "CREATE TRIGGER screening_runs_guard BEFORE INSERT OR UPDATE ON screening_runs "
    "FOR EACH ROW EXECUTE FUNCTION finvest_screening_runs_guard()",
    "CREATE TRIGGER screening_matches_guard BEFORE INSERT OR UPDATE ON screening_matches "
    "FOR EACH ROW EXECUTE FUNCTION finvest_screening_matches_guard()",
    # Nothing changes a match once written.
    "CREATE TRIGGER screening_matches_no_update BEFORE UPDATE ON screening_matches "
    "FOR EACH ROW EXECUTE FUNCTION finvest_screening_immutable()",
    "CREATE TRIGGER compliance_cases_guard BEFORE INSERT OR UPDATE ON compliance_cases "
    "FOR EACH ROW EXECUTE FUNCTION finvest_compliance_cases_guard()",
    "CREATE TRIGGER compliance_case_proposals_guard BEFORE INSERT OR UPDATE "
    "ON compliance_case_proposals FOR EACH ROW "
    "EXECUTE FUNCTION finvest_compliance_case_proposals_guard()",
    *(
        statement
        for table in TABLE_NAMES
        for statement in (
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION finvest_screening_immutable()",
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION finvest_screening_immutable()",
            f"REVOKE ALL ON {table} FROM PUBLIC",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"GRANT SELECT ON {table} TO {WORKER}",
            f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER} USING (true)",
            f"CREATE POLICY {table}_app_read ON {table} FOR SELECT TO {APP} USING ({STAFF})",
        )
    ),
    # Asking for a screen: a client for themselves when they hand in their identity check, or
    # a member of staff. The worker's sweep and payouts ask as the worker.
    f"GRANT SELECT, INSERT ON screening_runs TO {APP}",
    "CREATE POLICY screening_runs_app_insert ON screening_runs FOR INSERT "
    f"TO {APP} WITH CHECK (status = 'pending' AND ({STAFF} OR ("
    "public.finvest_actor_kind() = 'client' AND client_id = public.finvest_actor_id())))",
    f"GRANT INSERT, UPDATE (status, finished_at, provider, provider_reference, failure_code, "
    f"match_count, new_match_count) ON screening_runs TO {WORKER}",
    "CREATE POLICY screening_runs_worker_insert ON screening_runs FOR INSERT "
    f"TO {WORKER} WITH CHECK (status = 'pending')",
    "CREATE POLICY screening_runs_worker_update ON screening_runs FOR UPDATE "
    f"TO {WORKER} USING (true) WITH CHECK (true)",
    f"GRANT SELECT ON screening_matches TO {APP}",
    f"GRANT INSERT ON screening_matches TO {WORKER}",
    "CREATE POLICY screening_matches_worker_insert ON screening_matches FOR INSERT "
    f"TO {WORKER} WITH CHECK (true)",
    f"GRANT SELECT, UPDATE (status, outcome, closed_at, closed_by_proposal) "
    f"ON compliance_cases TO {APP}",
    "CREATE POLICY compliance_cases_app_update ON compliance_cases FOR UPDATE "
    f"TO {APP} USING ({STAFF}) WITH CHECK ({STAFF})",
    f"GRANT INSERT ON compliance_cases TO {WORKER}",
    "CREATE POLICY compliance_cases_worker_insert ON compliance_cases FOR INSERT "
    f"TO {WORKER} WITH CHECK (status = 'open')",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, decided_in) "
    f"ON compliance_case_proposals TO {APP}",
    "CREATE POLICY compliance_case_proposals_app_insert ON compliance_case_proposals "
    f"FOR INSERT TO {APP} WITH CHECK ({STAFF})",
    "CREATE POLICY compliance_case_proposals_app_update ON compliance_case_proposals "
    f"FOR UPDATE TO {APP} USING ({STAFF}) WITH CHECK ({STAFF})",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    for function in (RUNS_GUARD, MATCHES_GUARD, CASES_GUARD, PROPOSALS_GUARD, IMMUTABLE):
        op.execute(function)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM screening_runs) OR EXISTS (SELECT FROM compliance_cases) THEN
        RAISE EXCEPTION 'refusing to drop the screening and compliance record';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for table in reversed(TABLE_NAMES):
        op.execute(f"DROP TABLE {table}")
    for name in GUARDED:
        op.execute(f"DROP FUNCTION {name}()")
