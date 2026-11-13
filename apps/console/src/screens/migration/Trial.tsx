import { useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { Problem, Severity, Trial, TrialInput } from "./api.ts";
import { Pill } from "./Checks.tsx";
import { formatDay, formatMoment, problemLabel, SHEET_LABELS } from "./labels.ts";

interface TrialFormProps {
  busy: boolean;
  error: string | null;
  today: string;
  onRun: (input: TrialInput) => void;
  onCancel: () => void;
}

type Errors = Partial<Record<"clients" | "investments" | "asOf", string>>;

const FILE = "block w-full rounded-lg border border-border-strong bg-surface p-3 text-sm font-semibold text-ink file:mr-3 file:rounded-pill file:border-0 file:bg-surface-sunken file:px-3 file:py-1.5 file:font-bold";

/** Two CSV files and the day the old system's figures were taken. */
export function TrialForm({ busy, error, today, onRun, onCancel }: TrialFormProps) {
  const id = useId();
  const [clients, setClients] = useState<File | null>(null);
  const [investments, setInvestments] = useState<File | null>(null);
  const [asOf, setAsOf] = useState("");
  const [errors, setErrors] = useState<Errors>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const found: Errors = {};
    if (!clients) found.clients = "Choose the clients sheet.";
    else if (!clients.name.toLowerCase().endsWith(".csv")) found.clients = "Save the sheet as CSV first.";
    if (!investments) found.investments = "Choose the investments sheet.";
    else if (!investments.name.toLowerCase().endsWith(".csv")) found.investments = "Save the sheet as CSV first.";
    if (!asOf) found.asOf = "Give the day the Finvest 1.0 figures were taken.";
    else if (asOf > today) found.asOf = "This day has not happened yet.";
    setErrors(found);
    if (clients && investments && Object.keys(found).length === 0) onRun({ clients, investments, asOf });
  }

  const field = (name: keyof Errors) => ({
    id: `${id}-${name}`,
    "aria-invalid": errors[name] ? true : undefined,
    "aria-describedby": errors[name] ? `${id}-${name}-error` : undefined,
  });
  const message = (name: keyof Errors) =>
    errors[name] ? (
      <p id={`${id}-${name}-error`} className="m-0 text-sm font-semibold text-danger">
        {errors[name]}
      </p>
    ) : null;

  return (
    <form
      noValidate
      aria-label="Run a trial import"
      onSubmit={submit}
      className="flex flex-col gap-4 rounded-xl border-2 border-black bg-surface p-5"
    >
      <div>
        <h2 className="m-0 text-xl font-extrabold">Run a trial import</h2>
        <p className="m-0 text-md leading-normal text-muted">
          A trial saves nothing and changes no client. It checks both sheets and lists every problem, so you can fix the
          spreadsheet and run it again.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-clients`}>Clients sheet (CSV)</Label>
          <input type="file" accept=".csv,text/csv" className={FILE} onChange={(event) => setClients(event.target.files?.[0] ?? null)} {...field("clients")} />
          {message("clients")}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-investments`}>Investments sheet (CSV)</Label>
          <input type="file" accept=".csv,text/csv" className={FILE} onChange={(event) => setInvestments(event.target.files?.[0] ?? null)} {...field("investments")} />
          {message("investments")}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-asOf`}>Figures are as of</Label>
          <Input type="date" max={today} value={asOf} onChange={(event) => setAsOf(event.target.value)} {...field("asOf")} />
          {message("asOf")}
        </div>
      </div>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface p-3 text-md font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Checking the sheets" : "Run the trial"}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** From medium widths the list lines up as columns. Below that each problem stacks. */
const COLUMNS = "md:grid-cols-[7rem_5.5rem_minmax(0,11rem)_minmax(0,1fr)_5.5rem] md:gap-x-3 md:px-3";

const FILTERS: readonly { value: Severity | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "error", label: "Errors" },
  { value: "warning", label: "Warnings" },
];

function where(problem: Problem): string {
  return problem.row === null ? "Whole sheet" : `Row ${problem.row}`;
}

/** Every problem the latest trial found, errors first. */
export function Problems({ trial }: { trial: Trial }) {
  const [filter, setFilter] = useState<Severity | "all">("all");
  const shown = trial.problems.filter((problem) => filter === "all" || problem.severity === filter);
  const total = trial.errorCount + trial.warningCount;
  return (
    <section aria-labelledby="migration-problems" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="migration-problems" className="m-0 text-lg font-extrabold">
            Problems in the latest trial
          </h2>
          <p className="m-0 text-sm text-muted">
            Figures as of {formatDay(trial.asOf)}. Run by {trial.ranBy} on {formatMoment(trial.ranAt)}.
          </p>
        </div>
        {total > 0 ? (
          <div role="group" aria-label="Show" className="flex gap-1.5">
            {FILTERS.map((option) => (
              <Button
                key={option.value}
                variant={filter === option.value ? "primary" : "secondary"}
                aria-pressed={filter === option.value}
                onClick={() => setFilter(option.value)}
                className="h-9 px-3"
              >
                {option.label}
              </Button>
            ))}
          </div>
        ) : null}
      </div>
      {total === 0 ? (
        <p className="m-0 text-md leading-normal text-muted">The file is clean. No errors and no warnings.</p>
      ) : shown.length === 0 ? (
        <p className="m-0 text-md leading-normal text-muted">
          {filter === "error" ? "No errors. The file can be imported once the figures are checked." : "No warnings."}
        </p>
      ) : (
        <>
          <div aria-hidden="true" className={`hidden text-xs font-bold text-muted md:grid ${COLUMNS}`}>
            <span>Sheet</span>
            <span>Where</span>
            <span>Column</span>
            <span>What is wrong</span>
          </div>
          <ul aria-label="Problems found by the latest trial import" className="m-0 flex list-none flex-col p-0">
            {shown.map((problem, index) => (
              <li key={index} className={`grid grid-cols-[1fr_auto] items-start gap-x-3 gap-y-0.5 border-t border-divider py-2.5 text-md ${COLUMNS}`}>
                <span className="font-bold">{SHEET_LABELS[problem.sheet]}</span>
                <span className="col-start-1 whitespace-nowrap text-muted tabular-nums md:col-start-auto md:text-ink">{where(problem)}</span>
                <span className="col-start-1 min-w-0 md:col-start-auto">
                  {problem.column ? <code className="text-sm break-all">{problem.column}</code> : null}
                </span>
                <span className="col-start-1 md:col-start-auto">{problemLabel(problem)}</span>
                <span className="col-start-2 row-start-1 text-right md:col-start-auto md:row-start-auto">
                  <Pill tone={problem.severity === "error" ? "bg-danger-surface text-danger" : "bg-warning-surface text-warning-text"}>
                    {problem.severity === "error" ? "Error" : "Warning"}
                  </Pill>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {total > trial.problems.length ? (
        <p className="m-0 text-sm text-muted">
          Showing the first {trial.problems.length} of {total}. Fix these and run the trial again to see the rest.
        </p>
      ) : null}
      <p className="m-0 text-sm leading-normal text-muted">
        An error must be fixed in the spreadsheet. A warning does not block the import: the row comes across and is followed
        up afterwards.
      </p>
    </section>
  );
}
