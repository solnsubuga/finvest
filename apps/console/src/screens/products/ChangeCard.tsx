import { useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import type { CatalogueApi, Product, ProductChange } from "./api.ts";
import {
  ACTION_LABELS,
  formatBps,
  formatExact,
  formatStamp,
  formatTerm,
  INTEREST_PAID_LABELS,
  KIND_LABELS,
  PRODUCT_STATUS_LABELS,
} from "./format.ts";
import { Reach } from "./Reach.tsx";

interface ChangeCardProps {
  api: CatalogueApi;
  change: ProductChange;
  /** The product as it is now. Null for a new product. */
  current: Product | null;
  mine: boolean;
  /** Whether the person holds `products.change`. Without it the card only shows the change. */
  canDecide: boolean;
  busy: boolean;
  error: string | null;
  onApprove(): void;
  onSendBack(note: string): void;
  onWithdraw(): void;
}

interface Line {
  name: string;
  before: string | null;
  after: string;
}

function lines(change: ProductChange, current: Product | null): Line[] {
  const after: Line[] = [
    { name: "Code", before: null, after: change.productCode },
    { name: "Name", before: current?.name ?? null, after: change.name },
    { name: "Kind", before: current ? KIND_LABELS[current.kind] : null, after: KIND_LABELS[change.kind] },
    { name: "Currency", before: null, after: change.currency },
    { name: "Term", before: current ? formatTerm(current.termDays) : null, after: formatTerm(change.termDays) },
    { name: "Minimum", before: current ? formatExact(current.minimum) : null, after: formatExact(change.minimum) },
    {
      name: "Interest paid",
      before: current ? INTEREST_PAID_LABELS[current.interestPaid] : null,
      after: INTEREST_PAID_LABELS[change.interestPaid],
    },
    {
      name: "Status",
      before: current ? PRODUCT_STATUS_LABELS[current.status] : null,
      after: PRODUCT_STATUS_LABELS[change.productStatus],
    },
  ];
  if (change.action === "create") {
    const rate = change.firstRate;
    return [
      ...after,
      ...(rate
        ? [
            { name: "Rate before tax", before: null, after: formatBps(rate.annualRateBps) },
            { name: "Tax", before: null, after: formatBps(rate.taxBps) },
            { name: "Clients see", before: null, after: `${rate.clientRatePercent}% a year` },
          ]
        : []),
    ];
  }
  // For a change to an existing product, only what changes.
  return after.filter((line) => line.before !== null && line.before !== line.after);
}

/** A product change waiting for a second person, with what it changes and who it reaches. */
export function ChangeCard({ api, change, current, mine, canDecide, busy, error, onApprove, onSendBack, onWithdraw }: ChangeCardProps) {
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const noteId = `change-note-${change.id}`;
  const title = change.action === "create" ? change.name : (current?.name ?? change.name);
  return (
    <section
      aria-labelledby={`change-${change.id}`}
      className={cn("flex flex-col gap-3 rounded-xl bg-surface p-5", mine || !canDecide ? "border border-border" : "border-2 border-warning p-[19px]")}
    >
      <p className="m-0 text-xs font-bold text-muted">
        {ACTION_LABELS[change.action]} · {mine ? "waiting for a second person" : canDecide ? "waiting for you" : "waiting for approval"}
      </p>
      <h2 id={`change-${change.id}`} className="m-0 text-[17px] font-extrabold">
        {title}
      </h2>
      <dl className="m-0 flex flex-col gap-2.5">
        {lines(change, current).map((line) => (
          <div key={line.name} className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
            <dt className="text-muted">{line.name}</dt>
            <dd className="m-0 text-right font-bold tabular-nums">{line.before ? `${line.before} to ${line.after}` : line.after}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
          <dt className="text-muted">Proposed</dt>
          <dd className="m-0 text-right font-bold tabular-nums">{`${mine ? "By you" : "By a colleague"}, ${formatStamp(change.proposedAt)}`}</dd>
        </div>
      </dl>
      <p className="m-0 rounded-lg bg-surface-sunken p-3 text-sm leading-[1.45]">{change.reason}</p>
      {change.action === "create" ? (
        <p className="m-0 text-sm leading-[1.45] text-muted">
          It is created as a draft, which clients never see. Opening it to clients is a change of its own.
        </p>
      ) : (
        <Reach api={api} productCode={change.productCode} change={change.action === "edit" ? "terms" : change.action} />
      )}
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      {!canDecide ? null : mine ? (
        <Button variant="secondary" disabled={busy} onClick={onWithdraw}>
          Withdraw this change
        </Button>
      ) : sendingBack ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (note.trim()) onSendBack(note.trim());
          }}
        >
          <Label htmlFor={noteId}>Why it is going back</Label>
          <textarea
            id={noteId}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={3}
            className="w-full rounded-lg border border-border-strong bg-surface p-3 text-md focus-visible:border-black"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !note.trim()}>
              Send back
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={onApprove}>
            Approve as second person
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(true)}>
            Send back with a note
          </Button>
        </div>
      )}
    </section>
  );
}
