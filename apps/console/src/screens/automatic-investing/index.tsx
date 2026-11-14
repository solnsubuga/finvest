import { useCallback, useEffect, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { isStubAuth } from "@/auth/index.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { Panel, PanelRow, Pill } from "@/screens/migration/Checks.tsx";
import { autoinvestErrorMessage, notConnectedAutoinvestApi, RUN_STATUSES, type AutoinvestApi, type Overview, type Run, type RunStatus } from "./api.ts";
import { formatDay, formatShillings, NETWORK_LABELS, runNote, scheduleLabel, STATUS_LABELS } from "./labels.ts";
import { createHttpAutoinvestApi } from "./http.ts";
import { createStubAutoinvestApi, type StubCase } from "./stub.ts";

function stubCase(): StubCase {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" ? sample : "sample";
}

/**
 * Sample runs under `pnpm dev` or a preview build, like sign-in. `?sample=empty` and `?sample=error` show those states.
 * Otherwise the real routes, or "not connected" in a build that names no API.
 */
function defaultApi(): AutoinvestApi {
  if (isStubAuth()) return createStubAutoinvestApi(150, stubCase());
  const base = apiBaseUrl();
  if (!base) return notConnectedAutoinvestApi;
  const http = createHttp(base);
  return createHttpAutoinvestApi(http, createHttpDirectory(http));
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: Overview };
type Filter = RunStatus | "all";

const SUMMARY = "Plans that invest the same amount every week or month, and what each day's run did.";

const TONES: Record<RunStatus, string> = {
  due: "bg-surface-sunken text-ink",
  requested: "bg-warning-surface text-warning-text",
  paid: "bg-success-surface text-success",
  failed: "bg-danger-surface text-danger",
  missed: "bg-surface-sunken text-muted",
};

function today(now: Date): string {
  // The business day in Kampala, as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(now);
}

export function AutomaticInvestingScreen({ api, now = () => new Date() }: { api: AutoinvestApi; now?: () => Date }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [filter, setFilter] = useState<Filter>("all");
  const day = today(now());

  const fetchOverview = useCallback(
    (): Promise<Load> =>
      api.getOverview(day).then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: autoinvestErrorMessage(error) }),
      ),
    [api, day],
  );

  useEffect(() => {
    let current = true;
    void fetchOverview().then((result) => current && setLoad(result));
    return () => {
      current = false;
    };
  }, [fetchOverview]);

  return (
    <>
      <PageHeader title="Automatic investing" summary={SUMMARY} />
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading plans and runs
        </p>
      ) : load.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Automatic investing could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchOverview().then(setLoad);
            }}
          >
            Try again
          </Button>
        </section>
      ) : (
        <Ready overview={load.value} today={day} filter={filter} onFilter={setFilter} />
      )}
    </>
  );
}

function Card({ title, value, note, attention = false }: { title: string; value: string; note: string; attention?: boolean }) {
  return (
    <div className={cn("rounded-lg bg-surface p-4", attention ? "border-2 border-warning" : "border border-border")}>
      <div className="text-sm text-muted">{title}</div>
      <div className="text-2xl font-extrabold tracking-[-0.4px] tabular-nums">{value}</div>
      <div className={cn("text-xs font-bold", attention ? "text-warning-text" : "text-muted")}>{note}</div>
    </div>
  );
}

function count(runs: readonly Run[], status: RunStatus): number {
  return runs.filter((run) => run.status === status).length;
}

function Ready({
  overview,
  today,
  filter,
  onFilter,
}: {
  overview: Overview;
  today: string;
  filter: Filter;
  onFilter: (filter: Filter) => void;
}) {
  const runs = overview.runs;
  const todays = runs.filter((run) => run.dueOn === today);
  const open = count(todays, "due") + count(todays, "requested");
  const failed = count(runs, "failed");
  const missed = count(runs, "missed");
  const shown = filter === "all" ? runs : runs.filter((run) => run.status === filter);
  const week = `${formatDay(overview.from)} to ${formatDay(overview.to)}`;

  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
        <Card title="Plans running" value={String(overview.plans.active)} note={`${overview.plans.paused} paused by the client`} />
        <Card title="Runs today" value={String(todays.length)} note={open === 0 ? "None still open" : `${open} waiting for the client`} />
        <Card title="Invested this week" value={String(count(runs, "paid"))} note={`Runs from ${week}`} />
        <Card
          title="Failed or missed this week"
          value={String(failed + missed)}
          note={`${failed} failed, ${missed} missed`}
          attention={missed > 0}
        />
      </div>
      <div className="flex flex-wrap items-start gap-5">
        <section aria-label="Runs this week" className="flex min-w-0 flex-[3_1_560px] flex-col gap-3">
          <div role="group" aria-label="Show runs" className="flex flex-wrap gap-2">
            {(["all", ...RUN_STATUSES] as Filter[]).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={filter === option}
                onClick={() => onFilter(option)}
                className={cn(
                  "h-9 rounded-pill border px-3.5 text-sm font-bold",
                  filter === option ? "border-black bg-black text-white" : "border-border-strong bg-surface text-ink",
                )}
              >
                {option === "all" ? "All" : STATUS_LABELS[option]}
              </button>
            ))}
          </div>
          {runs.length === 0 ? (
            <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
              <h2 className="m-0 text-xl font-extrabold">No runs this week</h2>
              <p className="m-0 text-md leading-normal text-muted">
                Runs appear here on each plan&apos;s day, once the morning run has opened them. No client has a plan due between{" "}
                {week}.
              </p>
            </div>
          ) : shown.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
              No run this week is {STATUS_LABELS[filter as RunStatus].toLowerCase()}.
            </p>
          ) : (
            <RunsTable runs={shown} />
          )}
          {overview.cut ? (
            <p role="status" className="m-0 text-md font-bold text-warning-text">
              This week has more runs than the screen can show, so the oldest are left out. Tell an engineer.
            </p>
          ) : null}
        </section>
        <aside className="flex min-w-0 flex-[2_1_280px] flex-col gap-4">
          <Panel title="How a run works">
            <PanelRow name="Opened" value="Each morning, for every plan due that day" />
            <PanelRow name="Prompt sent" value="Between 8 am and 8 pm, Kampala time" />
            <PanelRow name="After a failed payment" value="One more try the next day" />
            <PanelRow name="A day not opened in time" value="Missed, never collected late" />
            <p className="m-0 text-sm leading-normal text-muted">
              This screen only reads. Staff cannot start, retry or change a run, because each run asks a client for money. A client
              pauses, resumes or stops their own plan in the app.
            </p>
          </Panel>
        </aside>
      </div>
    </>
  );
}

function RunsTable({ runs }: { runs: readonly Run[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Automatic investing runs this week">
      <table className="w-full min-w-[720px] border-collapse text-md">
        <caption className="sr-only">Automatic investing runs this week</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Day", "Client", "Plan", "Amount", "Status"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const note = runNote(run);
            return (
              <tr key={run.id} className="border-t border-divider align-top">
                <th scope="row" className="px-3 py-3 text-left font-bold whitespace-nowrap">
                  {formatDay(run.dueOn)}
                </th>
                <td className="px-3 py-3">
                  <div className="font-bold">{run.clientName}</div>
                  <div className="text-xs text-muted tabular-nums">{run.accountNumber}</div>
                </td>
                <td className="px-3 py-3">
                  <div>{run.productName}</div>
                  <div className="text-xs text-muted">
                    {scheduleLabel(run.frequency, run.day)}, {NETWORK_LABELS[run.network]}
                  </div>
                </td>
                <td className="px-3 py-3 font-bold whitespace-nowrap tabular-nums">{formatShillings(run.amount)}</td>
                <td className="px-3 py-3">
                  <Pill tone={TONES[run.status]}>{STATUS_LABELS[run.status]}</Pill>
                  {note ? <div className="mt-1.5 max-w-[240px] text-xs leading-snug text-muted">{note}</div> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function AutomaticInvestingPage() {
  const [api] = useState(defaultApi);
  return <AutomaticInvestingScreen api={api} />;
}
