import { cn } from "@/lib.ts";
import type { Complaint, Summary } from "./api.ts";
import { dueLabel, kampalaDate, OUTCOME_LABELS, shortDate, STATUS_LABELS, tenthsOfDays } from "./labels.ts";

/** The four figures at the top of the register, as on the canvas. */
export function Figures({ summary }: { summary: Summary }) {
  const { open, overdue, receivedThisQuarter, closedThisQuarter, upheldThisQuarter } = summary;
  return (
    <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
      <Figure
        name="Open"
        value={String(open)}
        note={overdue === 0 ? "None past its deadline" : `${overdue} past ${overdue === 1 ? "its" : "their"} deadline`}
        warn={overdue > 0}
      />
      <Figure
        name="Average time to resolve"
        value={summary.averageDaysToCloseTenths === null ? "None closed" : tenthsOfDays(summary.averageDaysToCloseTenths)}
        note={`Target ${summary.targetWorkingDays} working days`}
      />
      <Figure
        name="This quarter"
        value={receivedThisQuarter === 1 ? "1 complaint" : `${receivedThisQuarter} complaints`}
        note={`${closedThisQuarter} resolved`}
      />
      <Figure name="Upheld" value={String(upheldThisQuarter)} note="Client was right, this quarter" />
    </dl>
  );
}

function Figure({ name, value, note, warn = false }: { name: string; value: string; note: string; warn?: boolean }) {
  return (
    <div className="rounded-[18px] border border-border bg-surface p-4">
      <dt className="text-sm text-muted">{name}</dt>
      <dd className="m-0 text-[22px] font-extrabold tracking-[-0.4px] tabular-nums">{value}</dd>
      <dd className={cn("m-0 text-xs font-bold", warn ? "text-danger" : "text-muted")}>{note}</dd>
    </div>
  );
}

export function StatusChip({ complaint }: { complaint: Complaint }) {
  const [label, style] = complaint.overdue
    ? ["Past deadline", "bg-danger-surface text-danger"]
    : complaint.status === "closed"
      ? [complaint.outcome ? OUTCOME_LABELS[complaint.outcome] : "Closed", "bg-success-surface text-success"]
      : complaint.status === "escalated" || complaint.status === "waiting_for_client"
        ? [STATUS_LABELS[complaint.status], "bg-warning-surface text-warning-text"]
        : [STATUS_LABELS[complaint.status], "bg-surface-sunken text-black"];
  return <span className={cn("whitespace-nowrap rounded-pill px-2.5 py-1 text-2xs font-bold", style)}>{label}</span>;
}

interface RegisterTableProps {
  complaints: readonly Complaint[];
  selected: string | null;
  today: string;
  clientName: (clientId: string) => string;
  ownerName: (ownerId: string | null) => string;
  onSelect: (complaintId: string) => void;
}

export function RegisterTable({ complaints, selected, today, clientName, ownerName, onSelect }: RegisterTableProps) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[560px] border-collapse text-md">
        <caption className="sr-only">Complaints register</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Ref", "Client", "About", "Owner", "Due", "Status"].map((heading) => (
              <th key={heading} scope="col" className="px-2.5 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {complaints.map((complaint) => {
            const isSelected = complaint.id === selected;
            return (
              <tr
                key={complaint.id}
                aria-selected={isSelected}
                className={cn("border-t border-divider", isSelected ? "bg-paper" : "hover:bg-paper")}
              >
                <th scope="row" className="whitespace-nowrap px-2.5 py-3 text-left font-bold">
                  <button
                    type="button"
                    onClick={() => onSelect(complaint.id)}
                    aria-label={`${complaint.reference}, ${complaint.summary}`}
                    className="cursor-pointer underline-offset-2 hover:underline"
                  >
                    {complaint.reference}
                  </button>
                </th>
                <td className="px-2.5 py-3">{clientName(complaint.clientId)}</td>
                <td className="px-2.5 py-3">{complaint.summary}</td>
                <td className="px-2.5 py-3">{ownerName(complaint.ownerId)}</td>
                <td className={cn("whitespace-nowrap px-2.5 py-3", complaint.overdue && "font-bold text-danger")}>
                  {complaint.closedAt ? `Closed ${shortDate(kampalaDate(complaint.closedAt))}` : dueLabel(complaint.dueOn, today)}
                </td>
                <td className="px-2.5 py-3">
                  <StatusChip complaint={complaint} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
