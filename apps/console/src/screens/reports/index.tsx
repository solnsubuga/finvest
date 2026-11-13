import { Fragment, useCallback, useEffect, useState, type FormEvent } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  ACTOR_KINDS,
  auditErrorMessage,
  CATEGORIES,
  notConnectedAuditApi,
  OUTCOMES,
  type AuditApi,
  type AuditEvent,
  type AuditExport,
  type AuditFilters,
  type Facets,
} from "./api.ts";
import { createHttpAuditApi } from "./http.ts";
import {
  ACTOR_KIND_LABELS,
  actorIdLabel,
  CATEGORY_LABELS,
  describeActor,
  formatDetail,
  formatTime,
  narrowByActorLabel,
  OUTCOME_LABELS,
} from "./labels.ts";
import { createStubAuditApi } from "./stub.ts";

const SUMMARY = "Reports from the ledger, and the record of who did what and why.";
const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3 py-2.5 text-md font-medium text-ink";

type Load =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; events: AuditEvent[]; nextCursor: string | null };

/** Hands the browser the exported file. */
export function saveFile({ file, filename }: AuditExport) {
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

interface ReportsScreenProps {
  api: AuditApi;
  /** `audit.view`. Without it the screen says so and asks the API for nothing. */
  canReadAudit: boolean;
  save?: (exported: AuditExport) => void;
}

export function ReportsScreen({ api, canReadAudit, save = saveFile }: ReportsScreenProps) {
  return (
    <>
      <PageHeader title="Reports and audit" summary={SUMMARY} />
      {canReadAudit ? (
        <AuditTrail api={api} save={save} />
      ) : (
        <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Audit trail</h2>
          <p className="m-0 text-md leading-normal text-muted">Your role does not read the audit trail. Admins and compliance do.</p>
        </section>
      )}
    </>
  );
}

function AuditTrail({ api, save }: { api: AuditApi; save: (exported: AuditExport) => void }) {
  const [filters, setFilters] = useState<AuditFilters>({});
  const [draft, setDraft] = useState<AuditFilters>({});
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [facets, setFacets] = useState<Facets>({ actions: [], resourceTypes: [] });
  const [open, setOpen] = useState<string | null>(null);
  const [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [exporting, setExporting] = useState<{ busy: boolean; error: string | null; done: boolean }>({ busy: false, error: null, done: false });

  useEffect(() => {
    let current = true;
    void api.facets().then(
      (found) => {
        if (current) setFacets(found);
      },
      // The lists stay empty and the free text filters still work.
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [api]);

  const fetchFirst = useCallback(
    (applied: AuditFilters): Promise<Load> =>
      api.events(applied).then(
        (page) => ({ state: "ready", events: page.events, nextCursor: page.nextCursor }),
        (error: unknown) => ({ state: "failed", message: auditErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchFirst(filters).then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetchFirst, filters]);

  function apply(next: AuditFilters) {
    setDraft(next);
    setFilters(next);
    setLoad({ state: "loading" });
    setOpen(null);
    setMore({ busy: false, error: null });
    setExporting({ busy: false, error: null, done: false });
  }

  async function loadOlder() {
    if (load.state !== "ready" || !load.nextCursor) return;
    setMore({ busy: true, error: null });
    try {
      const page = await api.events(filters, load.nextCursor);
      setLoad({ state: "ready", events: [...load.events, ...page.events], nextCursor: page.nextCursor });
      setMore({ busy: false, error: null });
    } catch (error) {
      setMore({ busy: false, error: auditErrorMessage(error) });
    }
  }

  async function exportCsv() {
    setExporting({ busy: true, error: null, done: false });
    try {
      save(await api.exportCsv(filters));
      setExporting({ busy: false, error: null, done: true });
    } catch (error) {
      setExporting({ busy: false, error: auditErrorMessage(error), done: false });
    }
  }

  const narrowed = Object.values(filters).some((value) => typeof value === "string" && value.trim() !== "");

  return (
    <section aria-labelledby="audit-trail" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 id="audit-trail" className="m-0 text-xl font-extrabold">
            Audit trail
          </h2>
          <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
            Every change, approval, sign-in and refusal, newest first, in Kampala time. Personal details are left out when each event is written, so what you see is what is
            kept. Exports are recorded here too.
          </p>
        </div>
        <Button variant="secondary" disabled={exporting.busy || load.state === "loading"} onClick={() => void exportCsv()}>
          {exporting.busy ? "Exporting" : "Export CSV"}
        </Button>
      </div>
      {exporting.error ? (
        <p role="alert" className="m-0 max-w-[720px] rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
          {exporting.error}
        </p>
      ) : null}
      {exporting.done ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          Exported. The file holds every event that matches these filters, up to 10,000, and the export is now on the trail.
        </p>
      ) : null}

      <FilterForm draft={draft} facets={facets} onChange={setDraft} onApply={() => apply(draft)} onClear={() => apply({})} canClear={narrowed} />

      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the audit trail
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">The audit trail could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchFirst(filters).then(setLoad);
            }}
          >
            Try again
          </Button>
        </div>
      ) : load.events.length === 0 ? (
        <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">{narrowed ? "No events match" : "Nothing on the trail yet"}</h3>
          <p className="m-0 text-md leading-normal text-muted">
            {narrowed ? "Widen the dates or clear a filter." : "Events appear here as soon as anyone changes, approves or signs in to anything."}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div aria-hidden className="hidden px-4 text-2xs font-extrabold tracking-[0.6px] text-muted uppercase lg:grid lg:grid-cols-[180px_200px_minmax(0,1fr)_minmax(0,220px)_100px] lg:gap-4">
            <span>When</span>
            <span>Who</span>
            <span>What</span>
            <span>Record</span>
            <span>Outcome</span>
          </div>
          <ul aria-label="Events" className="m-0 flex list-none flex-col rounded-xl border border-border bg-surface p-0">
            {load.events.map((event) => (
              <EventRow
                key={event.id}
                event={event}
                open={open === event.id}
                onToggle={() => setOpen(open === event.id ? null : event.id)}
                onNarrow={(next) => apply({ ...filters, ...next })}
              />
            ))}
          </ul>
          <p className="m-0 text-sm text-muted">
            {load.events.length} {load.events.length === 1 ? "event" : "events"} shown{load.nextCursor ? ", older ones follow." : ". That is all of them."}
          </p>
          {more.error ? (
            <p role="alert" className="m-0 text-sm font-bold text-danger">
              {more.error}
            </p>
          ) : null}
          {load.nextCursor ? (
            <div>
              <Button variant="secondary" disabled={more.busy} onClick={() => void loadOlder()}>
                {more.busy ? "Loading" : "Show older events"}
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function Select<T extends string>({
  label,
  value,
  options,
  labels,
  onChange,
}: {
  label: string;
  value: T | undefined;
  options: readonly T[];
  labels?: Record<T, string>;
  onChange: (value: T | undefined) => void;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5 text-sm font-bold">
      {label}
      <select value={value ?? ""} onChange={(event) => onChange((event.target.value || undefined) as T | undefined)} className={INPUT}>
        <option value="">Any</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {labels ? labels[option] : option}
          </option>
        ))}
      </select>
    </label>
  );
}

function Text({ label, value, placeholder, onChange, type = "text" }: { label: string; value: string | undefined; placeholder?: string; onChange: (value: string) => void; type?: string }) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5 text-sm font-bold">
      {label}
      <input type={type} value={value ?? ""} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} className={INPUT} />
    </label>
  );
}

function FilterForm({
  draft,
  facets,
  onChange,
  onApply,
  onClear,
  canClear,
}: {
  draft: AuditFilters;
  facets: Facets;
  onChange: (next: AuditFilters) => void;
  onApply: () => void;
  onClear: () => void;
  canClear: boolean;
}) {
  const set = <K extends keyof AuditFilters>(key: K) => (value: AuditFilters[K] | string) => {
    const next = { ...draft };
    if (value === undefined || value === "") delete next[key];
    else next[key] = value as AuditFilters[K];
    onChange(next);
  };
  // An action already chosen stays in the list, even if the facets have not arrived.
  const actions = draft.action && !facets.actions.includes(draft.action) ? [draft.action, ...facets.actions] : facets.actions;
  const kinds = draft.resourceType && !facets.resourceTypes.includes(draft.resourceType) ? [draft.resourceType, ...facets.resourceTypes] : facets.resourceTypes;
  return (
    <form
      aria-label="Filter the audit trail"
      className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        onApply();
      }}
    >
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(150px,1fr))]">
        <Text label="From" type="date" value={draft.from} onChange={set("from")} />
        <Text label="To" type="date" value={draft.to} onChange={set("to")} />
        <Select label="Kind" value={draft.category} options={CATEGORIES} labels={CATEGORY_LABELS} onChange={set("category")} />
        <Select label="Outcome" value={draft.outcome} options={OUTCOMES} labels={OUTCOME_LABELS} onChange={set("outcome")} />
        <Select label="Done by" value={draft.actorKind} options={ACTOR_KINDS} labels={ACTOR_KIND_LABELS} onChange={set("actorKind")} />
        <Select label="Action" value={draft.action} options={actions} onChange={set("action")} />
        <Select label="Record type" value={draft.resourceType} options={kinds} onChange={set("resourceType")} />
        <Text label="Record ID" value={draft.resourceId} onChange={set("resourceId")} />
        <Text label="Staff or client ID" value={draft.actorId} onChange={set("actorId")} />
        <Text label="Request ID" value={draft.correlationId} onChange={set("correlationId")} />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit">Show events</Button>
        {canClear ? (
          <Button type="button" variant="secondary" onClick={onClear}>
            Clear filters
          </Button>
        ) : null}
      </div>
    </form>
  );
}

const OUTCOME_TONE: Record<AuditEvent["outcome"], string> = {
  succeeded: "bg-success-surface text-success",
  denied: "bg-warning-surface text-warning-text",
  failed: "bg-danger-surface text-danger",
};

function EventRow({
  event,
  open,
  onToggle,
  onNarrow,
}: {
  event: AuditEvent;
  open: boolean;
  onToggle: () => void;
  onNarrow: (filters: AuditFilters) => void;
}) {
  const record = event.resourceId ? `${event.resourceType} ${event.resourceId}` : event.resourceType;
  const details = Object.entries(event.details);
  return (
    <li className="border-b border-border last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="grid w-full cursor-pointer gap-1 bg-transparent px-4 py-3 text-left text-md text-ink hover:bg-surface-sunken lg:grid-cols-[180px_200px_minmax(0,1fr)_minmax(0,220px)_100px] lg:items-center lg:gap-4"
      >
        <span className="text-sm text-muted">{formatTime(event.occurredAt)}</span>
        <span className="font-bold">{describeActor(event)}</span>
        <span className="min-w-0 font-mono text-sm break-words">{event.action}</span>
        <span className="min-w-0 truncate text-sm text-muted">{record}</span>
        <span>
          <span className={cn("rounded-pill px-2 py-0.5 text-2xs font-bold", OUTCOME_TONE[event.outcome])}>{OUTCOME_LABELS[event.outcome]}</span>
        </span>
      </button>
      {open ? (
        <div className="flex flex-col gap-3 bg-surface-sunken px-4 py-3">
          <dl className="m-0 grid gap-x-4 gap-y-2 text-sm [grid-template-columns:max-content_minmax(0,1fr)]">
            <dt className="font-bold text-muted">Reason</dt>
            <dd className="m-0">{event.reason ?? "None given"}</dd>
            <dt className="font-bold text-muted">Kind</dt>
            <dd className="m-0">{CATEGORY_LABELS[event.category]}</dd>
            <dt className="font-bold text-muted">Record</dt>
            <dd className="m-0 font-mono break-all">{record}</dd>
            {event.actorId ? (
              <>
                <dt className="font-bold text-muted">{actorIdLabel(event.actorKind)}</dt>
                <dd className="m-0 font-mono break-all">{event.actorId}</dd>
              </>
            ) : null}
            <dt className="font-bold text-muted">Request ID</dt>
            <dd className="m-0 font-mono break-all">{event.correlationId ?? "None"}</dd>
            {details.map(([key, value]) => (
              <Fragment key={key}>
                <dt className="font-bold text-muted">{key}</dt>
                <dd className="m-0 font-mono break-all">{formatDetail(value)}</dd>
              </Fragment>
            ))}
          </dl>
          <div className="flex flex-wrap gap-2">
            {event.resourceId ? (
              <Button variant="secondary" onClick={() => onNarrow({ resourceType: event.resourceType, resourceId: event.resourceId ?? "" })}>
                Everything on this record
              </Button>
            ) : null}
            {event.correlationId ? (
              <Button variant="secondary" onClick={() => onNarrow({ correlationId: event.correlationId ?? "" })}>
                Everything in this request
              </Button>
            ) : null}
            {event.actorId ? (
              <Button variant="secondary" onClick={() => onNarrow({ actorId: event.actorId ?? "" })}>
                Everything by {narrowByActorLabel(event)}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

export default function ReportsPage() {
  const { session } = useAuth();
  const [api] = useState<AuditApi>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=empty` shows an empty trail, `?sample=busy` an export that is too large, and `?sample=offline` as if Finvest could not be reached.
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return notConnectedAuditApi;
      return createStubAuditApi({ id: session.staffId, name: session.name }, Date.now, sample === "empty" ? { empty: true } : sample === "busy" ? { exportLimit: 20 } : {});
    }
    const base = apiBaseUrl();
    return base ? createHttpAuditApi(createHttp(base)) : notConnectedAuditApi;
  });
  return <ReportsScreen api={api} canReadAudit={session ? can(session, "audit.view") : false} />;
}
