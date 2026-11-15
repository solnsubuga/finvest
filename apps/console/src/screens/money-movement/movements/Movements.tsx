import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { formatExact, formatMoment } from "@/screens/reconciliation/format.ts";
import { movementsErrorMessage, type Movement, type MovementFilter, type MovementPage, type MovementsApi } from "./api.ts";

const FILTERS: readonly { value: MovementFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "deposits", label: "Payments in" },
  { value: "payouts", label: "Payouts" },
  { value: "failed", label: "Failed" },
  { value: "unmatched", label: "Unmatched" },
];

const CHANNELS: Record<string, string> = {
  mtn: "MTN Mobile Money",
  airtel: "Airtel Money",
  bank_transfer: "Bank transfer",
  mobile_money: "Mobile money",
  bank: "Bank",
};

/** What each status means to a person, and how it is shown. */
const STATUSES: Record<string, { label: string; tone: "good" | "bad" | "wait" | "plain" }> = {
  confirmed: { label: "Received", tone: "good" },
  paid: { label: "Paid", tone: "good" },
  failed: { label: "Failed", tone: "bad" },
  uncertain: { label: "Outcome not known", tone: "wait" },
  in_review: { label: "Not found on statement yet", tone: "wait" },
  created: { label: "Started", tone: "plain" },
  sending: { label: "Waiting for the payer", tone: "plain" },
  awaiting_approval: { label: "Waiting for approval", tone: "plain" },
  screening: { label: "Being screened", tone: "plain" },
  held: { label: "Held for approval", tone: "wait" },
  queued: { label: "Queued", tone: "plain" },
  sent: { label: "Sent", tone: "plain" },
  cancelled: { label: "Cancelled", tone: "plain" },
};

const TONES = {
  good: "bg-success-surface text-success",
  bad: "bg-danger-surface text-danger",
  wait: "bg-warning-surface text-warning-text",
  plain: "bg-surface-sunken text-ink",
} as const;

const EMPTY: Record<MovementFilter, string> = {
  all: "No money has moved yet.",
  deposits: "No payments in yet.",
  payouts: "No payouts yet.",
  failed: "Nothing has failed.",
  unmatched: "Everything is matched: no bank transfer is waiting to be found on the statement, and every outcome is known.",
};

function status(movement: Movement) {
  const known = STATUSES[movement.status];
  if (known) return known;
  const words = movement.status.replace(/_/g, " ");
  return { label: words.charAt(0).toUpperCase() + words.slice(1), tone: "plain" as const };
}

const CELL =
  "px-2.5 py-2.5 max-sm:flex max-sm:gap-2 max-sm:p-0 max-sm:before:min-w-[96px] max-sm:before:text-sm max-sm:before:font-bold max-sm:before:text-muted max-sm:before:content-[attr(data-label)]";

/** Every payment in and payout, newest first, with a filter. Read only. */
export function Movements({ api }: { api: MovementsApi }) {
  const [show, setShow] = useState<MovementFilter>("all");
  return (
    <section aria-labelledby="every-movement" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 id="every-movement" className="m-0 text-xl font-extrabold">
            Every movement
          </h2>
          <p className="m-0 text-sm text-muted">Payments in and payouts, newest first. Unmatched means not yet found on the statement, or an outcome not yet known.</p>
        </div>
        <div role="group" aria-label="Show" className="flex flex-wrap gap-1.5">
          {FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              aria-pressed={show === filter.value}
              onClick={() => setShow(filter.value)}
              className={cn(
                "min-h-9 cursor-pointer rounded-pill border px-3 text-sm font-bold",
                show === filter.value ? "border-black bg-black text-white" : "border-border-strong bg-surface text-ink hover:bg-surface-sunken",
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>
      <MovementRows key={show} api={api} show={show} />
    </section>
  );
}

type Load =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; rows: Movement[]; nextAfter: string | null; loadingMore: boolean; moreFailed: string | null };

function MovementRows({ api, show }: { api: MovementsApi; show: MovementFilter }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    api.list(show, null).then(
      (page: MovementPage) => current && setLoad({ state: "ready", rows: page.movements, nextAfter: page.nextAfter, loadingMore: false, moreFailed: null }),
      (error: unknown) => current && setLoad({ state: "failed", message: movementsErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [api, show, attempt]);

  async function showMore() {
    if (load.state !== "ready" || !load.nextAfter) return;
    const shown = load;
    setLoad({ ...shown, loadingMore: true, moreFailed: null });
    try {
      const page = await api.list(show, shown.nextAfter);
      setLoad({ ...shown, rows: [...shown.rows, ...page.movements], nextAfter: page.nextAfter, loadingMore: false });
    } catch (error) {
      setLoad({ ...shown, loadingMore: false, moreFailed: movementsErrorMessage(error) });
    }
  }

  if (load.state === "loading") {
    return (
      <p role="status" className="m-0 text-md text-muted">
        Loading movements
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <div role="alert" className="flex flex-col items-start gap-2">
        <p className="m-0 text-md font-bold">Movements could not be loaded</p>
        <p className="m-0 text-md text-muted">{load.message}</p>
        <Button
          variant="secondary"
          onClick={() => {
            setLoad({ state: "loading" });
            setAttempt((n) => n + 1);
          }}
        >
          Try again
        </Button>
      </div>
    );
  }
  if (load.rows.length === 0) {
    return (
      <p role="status" className="m-0 text-md text-muted">
        {EMPTY[show]}
      </p>
    );
  }
  const title = FILTERS.find((filter) => filter.value === show)?.label ?? "All";
  return (
    <>
      <div className="sm:overflow-x-auto" tabIndex={0} role="region" aria-label={`Movements, ${title.toLowerCase()}, table`}>
        {/* On a phone each movement is a card: the cells stack and each value carries its heading. */}
        <table className="w-full border-collapse text-left text-md max-sm:block">
          <caption className="sr-only">Movements, {title.toLowerCase()}, newest first</caption>
          <thead className="max-sm:sr-only">
            <tr className="text-xs font-bold tracking-[0.6px] text-muted uppercase">
              {["Amount", "Type", "Client", "Channel", "Status", "Asked at"].map((heading) => (
                <th key={heading} scope="col" className="px-2.5 py-2 whitespace-nowrap">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="max-sm:flex max-sm:flex-col max-sm:gap-2.5">
            {load.rows.map((movement) => {
              const shown = status(movement);
              return (
                <tr key={`${movement.kind}:${movement.id}`} className="border-t border-divider max-sm:flex max-sm:flex-col max-sm:gap-1 max-sm:rounded-lg max-sm:border max-sm:p-3">
                  <th scope="row" className="px-2.5 py-2.5 font-bold whitespace-nowrap tabular-nums max-sm:p-0">
                    {movement.kind === "payout" ? "−" : "+"}
                    {formatExact(movement.amount)}
                  </th>
                  <td data-label="Type" className={CELL}>
                    {movement.kind === "deposit" ? "Payment in" : "Payout"}
                  </td>
                  <td data-label="Client" className={CELL}>
                    <Link to={`/clients?client=${encodeURIComponent(movement.clientId)}`} className="inline-flex min-h-6 items-center font-semibold underline-offset-2 hover:underline">
                      {movement.clientName ?? "Name not verified yet"}
                    </Link>
                  </td>
                  <td data-label="Channel" className={CELL}>
                    {CHANNELS[movement.channel] ?? movement.channel}
                  </td>
                  <td data-label="Status" className={CELL}>
                    <span className="flex flex-col items-start gap-0.5">
                      <span className={cn("rounded-pill px-2.5 py-0.5 text-xs font-bold whitespace-nowrap", TONES[shown.tone])}>{shown.label}</span>
                      {movement.failureCode ? <span className="text-xs text-muted">{movement.failureCode.replace(/_/g, " ")}</span> : null}
                    </span>
                  </td>
                  <td data-label="Asked at" className={cn(CELL, "whitespace-nowrap text-muted")}>
                    {formatMoment(movement.createdAt)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {load.moreFailed ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {load.moreFailed}
        </p>
      ) : null}
      {load.nextAfter ? (
        <Button variant="secondary" className="self-start" disabled={load.loadingMore} onClick={() => void showMore()}>
          {load.loadingMore ? "Loading" : "Show more"}
        </Button>
      ) : null}
    </>
  );
}
