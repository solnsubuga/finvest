import type { ReactNode } from "react";
import { cn } from "@/lib.ts";
import type { Overview, TotalLine } from "./api.ts";
import {
  difference,
  formatAmount,
  formatWithCurrency,
  lineLabel,
  lineResult,
  RESULT_LABELS,
  type LineResult,
} from "./labels.ts";

const TONES: Record<LineResult, string> = {
  agrees: "bg-success-surface text-success",
  look_into: "bg-warning-surface text-warning-text",
  waiting: "bg-warning-surface text-warning-text",
  signed_off: "bg-surface-sunken text-ink",
  not_entered: "bg-surface-sunken text-muted",
};

export function Pill({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", tone)}>{children}</span>;
}

function find(lines: readonly TotalLine[], key: string): TotalLine | undefined {
  return lines.find((line) => line.key === key);
}

function Card({ title, value, note, attention }: { title: string; value: string; note: string; attention: boolean }) {
  return (
    <div className={cn("rounded-lg bg-surface p-4", attention ? "border-2 border-warning" : "border border-border")}>
      <div className="text-sm text-muted">{title}</div>
      <div className="text-2xl font-extrabold tracking-[-0.4px] tabular-nums">{value}</div>
      <div className={cn("text-xs font-bold", attention ? "text-warning-text" : "text-muted")}>{note}</div>
    </div>
  );
}

function countCard(title: string, line: TotalLine | undefined): ReactNode {
  if (!line) return null;
  const imported = formatAmount(line.new, null);
  if (line.old === null) return <Card title={title} value={imported} note="Finvest 1.0 total not entered" attention={false} />;
  const gap = difference(line.old, line.new);
  return (
    <Card
      title={title}
      value={`${imported} of ${formatAmount(line.old, null)}`}
      note={gap === "0" ? "All matched" : `${formatAmount(gap.replace("-", ""), null)} ${gap.startsWith("-") ? "too many" : "missing"}`}
      attention={gap !== "0"}
    />
  );
}

/** The four figures at the top: what came across, and whether it matches the old system. */
export function Cards({ overview }: { overview: Overview }) {
  const interest = find(overview.lines, "total:interest_before_tax:UGX");
  const trial = overview.trial;
  const interestGap = interest?.old ? difference(interest.old, interest.new) : "0";
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
      {countCard("Clients", find(overview.lines, "total:clients"))}
      {countCard("Running investments", find(overview.lines, "total:running_investments"))}
      {interest ? (
        <Card
          title="Interest to date, before tax"
          value={formatWithCurrency(interest.new, "UGX")}
          note={
            interest.old === null
              ? "Finvest 1.0 total not entered"
              : interestGap === "0"
                ? "Agrees with Finvest 1.0"
                : `Differs by ${formatWithCurrency(interestGap.replace("-", ""), "UGX")}`
          }
          attention={interestGap !== "0"}
        />
      ) : null}
      {trial ? (
        <Card
          title="Problems in the file"
          value={trial.errorCount === 0 ? "No errors" : trial.errorCount === 1 ? "1 error" : `${formatAmount(String(trial.errorCount), null)} errors`}
          note={trial.warningCount === 1 ? "1 warning to follow up" : `${formatAmount(String(trial.warningCount), null)} warnings to follow up`}
          attention={trial.errorCount > 0}
        />
      ) : null}
    </div>
  );
}

/** Finvest 1.0 against the trial import, line by line. */
export function ChecksTable({ lines }: { lines: readonly TotalLine[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[600px] border-collapse text-md">
        <caption className="sr-only">Finvest 1.0 totals compared with the trial import</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Check", "Finvest 1.0", "Finvest 2.0", "Difference", "Result"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => {
            const result = lineResult(line);
            return (
              <tr key={line.key} className="border-t border-divider tabular-nums">
                <th scope="row" className="px-3 py-3 text-left font-bold">
                  {lineLabel(line)}
                </th>
                <td className="px-3 py-3">{line.old === null ? "" : formatAmount(line.old, line.currency)}</td>
                <td className="px-3 py-3">{formatAmount(line.new, line.currency)}</td>
                <td className="px-3 py-3">{line.old === null ? "" : formatAmount(difference(line.old, line.new), line.currency)}</td>
                <td className="px-3 py-3">
                  <Pill tone={TONES[result]}>{RESULT_LABELS[result]}</Pill>
                  {line.signedOff ? <div className="mt-1.5 max-w-[220px] text-xs leading-snug text-muted">{line.signedOff.reason}</div> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-5">
      <h2 className="m-0 text-lg font-extrabold">{title}</h2>
      {children}
    </section>
  );
}

export function PanelRow({ name, value }: { name: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
      <span className="text-muted">{name}</span>
      <span className="text-right font-bold tabular-nums">{value}</span>
    </div>
  );
}
