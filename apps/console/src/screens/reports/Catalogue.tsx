import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import {
  monthPeriod,
  periodProblem,
  quarterOf,
  quarterPeriod,
  reloadsSchedules,
  REPORT_KEYS,
  reportsErrorMessage,
  type Catalogue,
  type Currency,
  type Decision,
  type Frequency,
  type Period,
  type Report,
  type ReportFile,
  type ReportKey,
  type ReportsApi,
  type Schedule,
  type Schedules,
  type ScheduleStatus,
} from "./catalogue.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3 py-2.5 text-md font-medium text-ink";
const LABEL = "flex min-w-0 flex-col gap-1.5 text-sm font-bold";
const GROUPED = new Intl.NumberFormat("en-GB");

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

/** What a link in a schedule's email asks the screen to open. */
export interface ReportLink {
  report: string;
  from: string | null;
  to: string | null;
  currency: string | null;
}

export const FREQUENCY_LABELS: Record<Frequency, string> = {
  daily: "Daily",
  weekly: "Weekly, on Mondays",
  monthly: "Monthly, on the 1st",
  quarterly: "Quarterly, on the first day of the quarter",
};

const SENT: Record<Frequency, string> = {
  daily: "Emailed daily at 07:00",
  weekly: "Emailed every Monday at 07:00",
  monthly: "Emailed on the 1st of each month at 07:00",
  quarterly: "Emailed on the first day of each quarter at 07:00",
};

const STATUS: Record<ScheduleStatus, { label: string; tone: string }> = {
  proposed: { label: "Waiting for approval", tone: "bg-warning-surface text-warning-text" },
  active: { label: "Running", tone: "bg-success-surface text-success" },
  declined: { label: "Declined", tone: "bg-surface-sunken text-muted" },
  withdrawn: { label: "Withdrawn", tone: "bg-surface-sunken text-muted" },
  stopped: { label: "Stopped", tone: "bg-surface-sunken text-muted" },
};

const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const TIME = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });

function formatDay(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

function names(list: readonly string[]): string {
  const shown = list.filter(Boolean);
  if (shown.length === 0) return "nobody";
  if (shown.length === 1) return shown[0] as string;
  if (shown.length === 2) return `${shown[0]} and ${shown[1]}`;
  return `${shown[0]} and ${GROUPED.format(shown.length - 1)} others`;
}

/** The line under a report's name: how it is sent today, if at all. */
export function scheduleLine(key: ReportKey, schedules: Load<Schedules>): string {
  if (schedules.state === "loading") return "Loading schedules";
  if (schedules.state === "failed") return "Schedules could not be loaded";
  const mine = schedules.value.schedules.filter((schedule) => schedule.reportKey === key);
  const running = mine.filter((schedule) => schedule.status === "active");
  const waiting = mine.filter((schedule) => schedule.status === "proposed").length;
  const parts: string[] = [];
  const first = running[0];
  if (first) {
    parts.push(`${SENT[first.frequency]} to ${names(first.recipientNames)}`);
    if (running.length > 1) parts.push(`and ${GROUPED.format(running.length - 1)} more ${running.length === 2 ? "schedule" : "schedules"}`);
  }
  if (waiting) parts.push(`${GROUPED.format(waiting)} waiting for approval`);
  return parts.length ? parts.join(", ") : "Not scheduled";
}

interface ReportsSectionProps {
  api: ReportsApi;
  /** The reader's own staff ID. */
  me: string;
  save: (file: ReportFile) => void;
  newKey: () => string;
  link?: ReportLink | null;
}

type Panel = { key: ReportKey; kind: "download" | "schedule" } | null;

export function ReportsSection({ api, me, save, newKey, link = null }: ReportsSectionProps) {
  const [catalogue, setCatalogue] = useState<Load<Catalogue>>({ state: "loading" });
  const [schedules, setSchedules] = useState<Load<Schedules>>({ state: "loading" });
  const [panel, setPanel] = useState<Panel>(link && isKey(link.report) ? { key: link.report, kind: "download" } : null);

  const fetchCatalogue = useCallback(
    (): Promise<Load<Catalogue>> =>
      api.catalogue().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: reportsErrorMessage(error) }),
      ),
    [api],
  );
  const fetchSchedules = useCallback(
    (): Promise<Load<Schedules>> =>
      api.schedules().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: reportsErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchCatalogue().then((next) => {
      if (current) setCatalogue(next);
    });
    void fetchSchedules().then((next) => {
      if (current) setSchedules(next);
    });
    return () => {
      current = false;
    };
  }, [fetchCatalogue, fetchSchedules]);

  const reloadSchedules = useCallback(async () => {
    setSchedules(await fetchSchedules());
  }, [fetchSchedules]);

  const toggle = (key: ReportKey, kind: "download" | "schedule") =>
    setPanel(panel?.key === key && panel.kind === kind ? null : { key, kind });

  return (
    <>
      <section aria-labelledby="reports" className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 id="reports" className="m-0 text-xl font-extrabold">
            Reports
          </h2>
          <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
            Files name clients by their Finvest ID only, and every download is recorded on the audit trail.
          </p>
        </div>
        {catalogue.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading reports
          </p>
        ) : catalogue.state === "failed" ? (
          <Problem
            title="Reports could not be loaded"
            message={catalogue.message}
            onRetry={() => {
              setCatalogue({ state: "loading" });
              void fetchCatalogue().then(setCatalogue);
            }}
          />
        ) : catalogue.value.reports.length === 0 ? (
          <Quiet title="No reports for your role">Each report needs the screen it comes from. Ask an admin if you need one.</Quiet>
        ) : (
          <>
            {link && !catalogue.value.reports.some((report) => report.key === link.report) ? (
              <p role="status" className="m-0 max-w-[720px] rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
                The link you followed is for a report your role cannot download.
              </p>
            ) : null}
            <ul aria-label="Reports" className="m-0 grid list-none grid-cols-1 items-start gap-4 p-0 md:grid-cols-2 2xl:grid-cols-3">
              {catalogue.value.reports.map((report) => (
                <ReportCard
                  key={report.key}
                  report={report}
                  today={catalogue.value.today}
                  line={scheduleLine(report.key, schedules)}
                  panel={panel?.key === report.key ? panel.kind : null}
                  onToggle={(kind) => toggle(report.key, kind)}
                  api={api}
                  save={save}
                  newKey={newKey}
                  schedules={schedules}
                  link={link?.report === report.key ? link : null}
                  onProposed={reloadSchedules}
                />
              ))}
            </ul>
          </>
        )}
      </section>
      <SchedulesList api={api} me={me} schedules={schedules} newKey={newKey} onReload={reloadSchedules} />
    </>
  );
}

function isKey(value: string): value is ReportKey {
  return (REPORT_KEYS as readonly string[]).includes(value);
}

function ReportCard({
  report,
  today,
  line,
  panel,
  onToggle,
  api,
  save,
  newKey,
  schedules,
  link,
  onProposed,
}: {
  report: Report;
  today: string;
  line: string;
  panel: "download" | "schedule" | null;
  onToggle: (kind: "download" | "schedule") => void;
  api: ReportsApi;
  save: (file: ReportFile) => void;
  newKey: () => string;
  schedules: Load<Schedules>;
  link: ReportLink | null;
  onProposed: () => Promise<void>;
}) {
  const card = useRef<HTMLLIElement>(null);
  useEffect(() => {
    // Opened from an emailed link: bring the card into view.
    if (link) card.current?.scrollIntoView?.({ block: "start" });
  }, [link]);
  const headingId = `report-${report.key}`;
  return (
    <li ref={card} aria-labelledby={headingId} className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-col gap-1.5">
        <h3 id={headingId} className="m-0 text-[17px] font-extrabold">
          {report.name}
        </h3>
        <p className="m-0 text-md leading-normal text-muted">{report.about}</p>
        <p className={cn("m-0 text-sm font-bold", line === "Not scheduled" ? "text-muted" : "text-ink")}>{line}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant={panel === "download" ? "primary" : "secondary"} aria-expanded={panel === "download"} onClick={() => onToggle("download")}>
          Download
        </Button>
        <Button variant={panel === "schedule" ? "primary" : "secondary"} aria-expanded={panel === "schedule"} onClick={() => onToggle("schedule")}>
          Schedule
        </Button>
      </div>
      {panel === "download" ? <DownloadForm report={report} today={today} api={api} save={save} link={link} /> : null}
      {panel === "schedule" ? <ScheduleForm report={report} api={api} newKey={newKey} schedules={schedules} onProposed={onProposed} /> : null}
    </li>
  );
}

function startingPeriod(report: Report, link: ReportLink | null): Period {
  const currency: Currency = link?.currency === "USD" ? "USD" : "UGX";
  if (link?.from && link.to) return { from: link.from, to: link.to, currency };
  return { from: report.from, to: report.to, currency };
}

function DownloadForm({ report, today, api, save, link }: { report: Report; today: string; api: ReportsApi; save: (file: ReportFile) => void; link: ReportLink | null }) {
  const [period, setPeriod] = useState<Period>(() => startingPeriod(report, link));
  const [state, setState] = useState<{ busy: boolean; error: string | null; done: string | null }>({ busy: false, error: null, done: null });
  const problem = periodProblem(report.period, period.from, period.to, today);

  async function download() {
    setState({ busy: true, error: null, done: null });
    try {
      const file = await api.download(report.key, period);
      save(file);
      setState({ busy: false, error: null, done: `Downloaded ${file.filename}. The download is on the audit trail.` });
    } catch (error) {
      setState({ busy: false, error: reportsErrorMessage(error), done: null });
    }
  }

  return (
    <form
      aria-label={`Download ${report.name}`}
      className="flex flex-col gap-3 border-t border-divider pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        void download();
      }}
    >
      <PeriodFields report={report} today={today} period={period} onChange={(next) => setPeriod({ ...period, ...next })} />
      {report.byCurrency ? <CurrencyField value={period.currency} onChange={(currency) => setPeriod({ ...period, currency })} /> : null}
      {problem ? (
        <p className="m-0 text-sm font-semibold text-danger">{problem}</p>
      ) : null}
      {state.error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {state.error}
        </p>
      ) : null}
      {state.done ? (
        <p role="status" className="m-0 rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold break-words">
          {state.done}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={state.busy || problem !== null}>
          {state.busy ? "Making the file" : "Download CSV"}
        </Button>
      </div>
    </form>
  );
}

function PeriodFields({ report, today, period, onChange }: { report: Report; today: string; period: Period; onChange: (next: { from: string; to: string }) => void }) {
  switch (report.period) {
    case "now":
      return <p className="m-0 text-md">As things stand today, {formatDay(today)}.</p>;
    case "day":
      return (
        <label className={LABEL}>
          Day
          <input type="date" max={today} value={period.from} className={INPUT} onChange={(event) => onChange({ from: event.target.value, to: event.target.value })} />
        </label>
      );
    case "month":
      return (
        <label className={LABEL}>
          Month
          <input
            type="month"
            max={today.slice(0, 7)}
            value={period.from.slice(0, 7)}
            className={INPUT}
            onChange={(event) => {
              const month = monthPeriod(event.target.value);
              if (month) onChange(month);
            }}
          />
        </label>
      );
    case "quarter":
      return <QuarterField today={today} from={period.from} onChange={onChange} />;
    default:
      return (
        <div className="flex flex-col gap-1.5">
          <div className="grid grid-cols-2 gap-3">
            <label className={LABEL}>
              From
              <input type="date" value={period.from} max={report.period === "days" ? today : undefined} className={INPUT} onChange={(event) => onChange({ from: event.target.value, to: period.to })} />
            </label>
            <label className={LABEL}>
              To
              <input type="date" value={period.to} className={INPUT} onChange={(event) => onChange({ from: period.from, to: event.target.value })} />
            </label>
          </div>
          <p className="m-0 text-xs text-muted">{report.period === "days" ? "Up to 31 days, in Kampala time." : "Up to 92 days. Days ahead are what is due so far."}</p>
        </div>
      );
  }
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function QuarterField({ today, from, onChange }: { today: string; from: string; onChange: (next: { from: string; to: string }) => void }) {
  // The current quarter and the seven before it.
  const options: { value: string; label: string; from: string; to: string }[] = [];
  let { year, quarter } = quarterOf(today);
  for (let index = 0; index < 8; index += 1) {
    const span = quarterPeriod(year, quarter);
    const first = MONTHS[(quarter - 1) * 3] ?? "";
    const last = MONTHS[quarter * 3 - 1] ?? "";
    options.push({ value: span.from, label: `Q${quarter} ${year}, ${first} to ${last}${index === 0 ? " (so far)" : ""}`, ...span });
    quarter -= 1;
    if (quarter === 0) {
      quarter = 4;
      year -= 1;
    }
  }
  return (
    <label className={LABEL}>
      Quarter
      <select
        value={from}
        className={INPUT}
        onChange={(event) => {
          const chosen = options.find((option) => option.value === event.target.value);
          if (chosen) onChange({ from: chosen.from, to: chosen.to });
        }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function CurrencyField({ value, onChange }: { value: Currency; onChange: (currency: Currency) => void }) {
  return (
    <label className={LABEL}>
      Currency
      <select value={value} className={INPUT} onChange={(event) => onChange(event.target.value === "USD" ? "USD" : "UGX")}>
        <option value="UGX">Uganda shillings (UGX)</option>
        <option value="USD">US dollars (USD)</option>
      </select>
    </label>
  );
}

function ScheduleForm({
  report,
  api,
  newKey,
  schedules,
  onProposed,
}: {
  report: Report;
  api: ReportsApi;
  newKey: () => string;
  schedules: Load<Schedules>;
  onProposed: () => Promise<void>;
}) {
  const frequencies = schedules.state === "ready" ? (schedules.value.frequencies[report.key] ?? []) : [];
  const people = schedules.state === "ready" ? schedules.value.people.filter((person) => person.reports.includes(report.key)) : [];
  const [frequency, setFrequency] = useState<Frequency | null>(null);
  const [currency, setCurrency] = useState<Currency>("UGX");
  const [chosen, setChosen] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ busy: boolean; error: string | null; done: boolean }>({ busy: false, error: null, done: false });
  const pending = useRef<{ request: string; key: string } | null>(null);
  const picked = frequency ?? frequencies[0] ?? null;

  if (schedules.state === "loading") return <p className="m-0 border-t border-divider pt-3 text-md text-muted">Loading who can be sent this report</p>;
  if (schedules.state === "failed") {
    return (
      <p role="alert" className="m-0 border-t border-divider pt-3 text-md font-semibold text-danger">
        {schedules.message}
      </p>
    );
  }
  if (state.done) {
    return (
      <p role="status" className="m-0 rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
        Proposed. Nothing is sent until a different member of staff approves it under Schedules below, or in the review queue.
      </p>
    );
  }

  async function propose() {
    if (!picked) return;
    const proposal = { reportKey: report.key, frequency: picked, currency, recipients: [...chosen].sort(), reason: reason.trim() };
    const request = JSON.stringify(proposal);
    if (pending.current?.request !== request) pending.current = { request, key: newKey() };
    setState({ busy: true, error: null, done: false });
    try {
      await api.propose(proposal, pending.current.key);
      pending.current = null;
      setState({ busy: false, error: null, done: true });
      await onProposed();
    } catch (error) {
      setState({ busy: false, error: reportsErrorMessage(error), done: false });
    }
  }

  const ready = picked !== null && chosen.length > 0 && reason.trim() !== "";
  const idPrefix = `schedule-${report.key}`;
  return (
    <form
      aria-label={`Schedule ${report.name}`}
      className="flex flex-col gap-3 border-t border-divider pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        void propose();
      }}
    >
      <p className="m-0 text-sm leading-normal text-muted">
        Each send is an email at 07:00 Kampala time with a link to the file, never the file itself. The link opens here, so only people whose role can download the report
        are offered. A different member of staff must approve the schedule before anything is sent.
      </p>
      <label className={LABEL}>
        How often
        <select value={picked ?? ""} className={INPUT} onChange={(event) => setFrequency(event.target.value as Frequency)}>
          {frequencies.map((option) => (
            <option key={option} value={option}>
              {FREQUENCY_LABELS[option]}
            </option>
          ))}
        </select>
      </label>
      {report.byCurrency ? <CurrencyField value={currency} onChange={setCurrency} /> : null}
      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 p-0 text-sm font-bold">Send to</legend>
        {people.length === 0 ? (
          <p className="m-0 text-sm text-muted">Nobody on the team can be sent this report.</p>
        ) : (
          people.map((person) => (
            <label key={person.id} className="flex items-center gap-2.5 text-md">
              <input
                type="checkbox"
                className="size-4 accent-black"
                checked={chosen.includes(person.id)}
                onChange={(event) => setChosen(event.target.checked ? [...chosen, person.id] : chosen.filter((id) => id !== person.id))}
              />
              {person.name}
            </label>
          ))
        )}
      </fieldset>
      <label className={LABEL} htmlFor={`${idPrefix}-reason`}>
        Reason
        <textarea id={`${idPrefix}-reason`} rows={2} maxLength={500} value={reason} className={INPUT} onChange={(event) => setReason(event.target.value)} />
      </label>
      {state.error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {state.error}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={state.busy || !ready}>
          {state.busy ? "Proposing" : "Propose schedule"}
        </Button>
      </div>
    </form>
  );
}

const DECISION_LABELS: Record<Decision, { button: string; busy: string; done: string }> = {
  approve: { button: "Approve", busy: "Approving", done: "Approved. The first email goes at 07:00 on the next day it is due." },
  decline: { button: "Decline", busy: "Declining", done: "Declined. Nothing will be sent." },
  withdraw: { button: "Withdraw", busy: "Withdrawing", done: "Withdrawn. Nothing will be sent." },
  stop: { button: "Stop sending", busy: "Stopping", done: "Stopped. No more emails will be sent." },
};

function choicesFor(schedule: Schedule): Decision[] {
  if (schedule.status === "active") return ["stop"];
  if (schedule.status !== "proposed") return [];
  return schedule.yours ? ["withdraw"] : ["approve", "decline"];
}

function SchedulesList({ api, me, schedules, newKey, onReload }: { api: ReportsApi; me: string; schedules: Load<Schedules>; newKey: () => string; onReload: () => Promise<void> }) {
  const [note, setNote] = useState<string | null>(null);
  return (
    <section aria-labelledby="report-schedules" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id="report-schedules" className="m-0 text-xl font-extrabold">
          Schedules
        </h2>
        <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
          Waiting and running schedules first. One person proposes and a different person approves. Anyone who can download the report can stop it.
        </p>
      </div>
      {note ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {note}
        </p>
      ) : null}
      {schedules.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading schedules
        </p>
      ) : schedules.state === "failed" ? (
        <Problem title="Schedules could not be loaded" message={schedules.message} onRetry={() => void onReload()} />
      ) : schedules.value.schedules.length === 0 ? (
        <Quiet title="No schedules yet">Choose Schedule on a report above to have it emailed to the team.</Quiet>
      ) : (
        <ul aria-label="Schedules" className="m-0 flex list-none flex-col rounded-xl border border-border bg-surface p-0">
          {schedules.value.schedules.map((schedule) => (
            <ScheduleRow key={schedule.id} schedule={schedule} me={me} api={api} newKey={newKey} onDone={async (message) => { setNote(message); await onReload(); }} onStale={onReload} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ScheduleRow({
  schedule,
  me,
  api,
  newKey,
  onDone,
  onStale,
}: {
  schedule: Schedule;
  me: string;
  api: ReportsApi;
  newKey: () => string;
  onDone: (message: string) => Promise<void>;
  onStale: () => Promise<void>;
}) {
  const [choice, setChoice] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const pending = useRef<{ request: string; key: string } | null>(null);
  const status = STATUS[schedule.status];
  const choices = choicesFor(schedule);
  const proposer = schedule.proposedBy === me ? "you" : (schedule.proposedByName ?? "a colleague");

  async function decide(decision: Decision) {
    const request = JSON.stringify({ decision, reason: reason.trim() });
    if (pending.current?.request !== request) pending.current = { request, key: newKey() };
    setState({ busy: true, error: null });
    try {
      await api.decide(schedule.id, decision, reason.trim(), pending.current.key);
      pending.current = null;
      setState({ busy: false, error: null });
      setChoice(null);
      setReason("");
      await onDone(`${schedule.reportName}: ${DECISION_LABELS[decision].done}`);
    } catch (error) {
      setState({ busy: false, error: reportsErrorMessage(error) });
      if (reloadsSchedules(error)) await onStale();
    }
  }

  return (
    <li aria-label={`${schedule.reportName}, ${status.label}`} className="flex flex-col gap-3 border-b border-divider p-4 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="m-0 text-md font-extrabold">
            {schedule.reportName}
            {schedule.currency ? <span className="font-bold text-muted">, {schedule.currency}</span> : null}
          </p>
          <p className="m-0 text-md">
            {SENT[schedule.frequency]} to {names(schedule.recipientNames)}
          </p>
          <p className="m-0 text-sm text-muted">
            Proposed by {proposer} on {formatTime(schedule.proposedAt)}: “{schedule.reason}”
          </p>
          {schedule.decidedAt && schedule.status !== "proposed" ? (
            <p className="m-0 text-sm text-muted">
              {schedule.status === "withdrawn" ? "Withdrawn" : schedule.status === "declined" ? "Declined" : "Approved"} on {formatTime(schedule.decidedAt)}
              {schedule.decisionReason ? `: “${schedule.decisionReason}”` : ""}
            </p>
          ) : null}
          {schedule.stoppedAt ? <p className="m-0 text-sm text-muted">Stopped on {formatTime(schedule.stoppedAt)}</p> : null}
          {schedule.status === "active" ? (
            <p className="m-0 text-sm text-muted">{schedule.lastSentAt ? `Last sent ${formatTime(schedule.lastSentAt)}` : "Not sent yet"}</p>
          ) : null}
        </div>
        <span className={cn("shrink-0 rounded-pill px-2.5 py-1 text-xs font-bold", status.tone)}>{status.label}</span>
      </div>
      {choices.length ? (
        <div className="flex flex-wrap gap-2">
          {choices.map((decision) => (
            <Button
              key={decision}
              variant={choice === decision ? "primary" : "secondary"}
              aria-expanded={choice === decision}
              onClick={() => {
                setChoice(choice === decision ? null : decision);
                setState({ busy: false, error: null });
              }}
            >
              {DECISION_LABELS[decision].button}
            </Button>
          ))}
        </div>
      ) : null}
      {schedule.status === "proposed" && schedule.yours ? <p className="m-0 text-sm text-muted">A different member of staff must approve or decline it.</p> : null}
      {choice ? (
        <form
          aria-label={`${DECISION_LABELS[choice].button} ${schedule.reportName}`}
          className="flex max-w-[560px] flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void decide(choice);
          }}
        >
          <label className={LABEL} htmlFor={`decide-${schedule.id}`}>
            Reason
            <textarea id={`decide-${schedule.id}`} rows={2} maxLength={500} value={reason} className={INPUT} onChange={(event) => setReason(event.target.value)} />
          </label>
          {choice === "approve" ? <p className="m-0 text-xs text-muted">You will be asked to confirm it is you.</p> : null}
          {state.error ? (
            <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
              {state.error}
            </p>
          ) : null}
          <div>
            <Button type="submit" disabled={state.busy || reason.trim() === ""}>
              {state.busy ? DECISION_LABELS[choice].busy : DECISION_LABELS[choice].button}
            </Button>
          </div>
        </form>
      ) : null}
    </li>
  );
}

function Problem({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h3 className="m-0 text-[17px] font-extrabold">{title}</h3>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

function Quiet({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
      <h3 className="m-0 text-[17px] font-extrabold">{title}</h3>
      <p className="m-0 text-md leading-normal text-muted">{children}</p>
    </div>
  );
}
