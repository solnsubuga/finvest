import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  CatalogueError,
  catalogueErrorMessage,
  type CatalogueApi,
  type NewProposal,
  type Product,
  type Proposal,
  type Rate,
} from "../products/api.ts";
import { formatBps, formatDay, formatStamp, parsePercent, previewClientRate } from "../products/format.ts";
import { connectedCatalogueApi } from "../products/http.ts";
import { createStubCatalogueApi } from "../products/stub.ts";

const SUMMARY = "Every rate change is proposed by one person, approved by another, and starts on a set date.";

interface Book {
  products: readonly Product[];
  waiting: readonly Proposal[];
  history: ReadonlyMap<string, readonly Rate[]>;
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; book: Book };

interface RatesScreenProps {
  api: CatalogueApi;
  /** The signed-in person's staff ID, to tell their own proposals from a colleague's. */
  me: string;
  /** Whether the person holds `rates.change`. Without it the screen only shows rates. */
  canChange: boolean;
  /** The Kampala business day, as a calendar date. */
  today?: () => string;
}

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

export function RatesScreen({ api, me, canChange, today = kampalaToday }: RatesScreenProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [round, setRound] = useState(0);

  const fetchBook = useCallback(async (): Promise<Load> => {
    try {
      const [products, waiting] = await Promise.all([api.listProducts(), api.listProposals("proposed")]);
      const histories = await Promise.all(products.map((product) => api.rateHistory(product.code)));
      return {
        state: "ready",
        book: { products, waiting, history: new Map(products.map((product, index) => [product.code, histories[index] ?? []])) },
      };
    } catch (error) {
      return { state: "failed", message: catalogueErrorMessage(error) };
    }
  }, [api]);

  useEffect(() => {
    let current = true;
    void fetchBook().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchBook]);

  async function run(on: string, command: () => Promise<unknown>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      await command();
      const fresh = await fetchBook();
      setLoad(fresh);
      setRound((count) => count + 1);
      return true;
    } catch (error) {
      setFailed({ on, message: catalogueErrorMessage(error) });
      if (error instanceof CatalogueError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchBook();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  if (load.state === "loading") {
    return (
      <>
        <PageHeader title="Rates" summary={SUMMARY} />
        <p role="status" className="m-0 text-md text-muted">
          Loading rates
        </p>
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        <PageHeader title="Rates" summary={SUMMARY} />
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Rates could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchBook().then(setLoad)}>
            Try again
          </Button>
        </section>
      </>
    );
  }

  const { book } = load;
  const names = new Map(book.products.map((product) => [product.code, product.name]));
  const day = today();
  return (
    <>
      <PageHeader title="Rates" summary={SUMMARY} />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[3_1_520px] flex-col gap-4">
          <History book={book} today={day} />
        </div>
        <aside aria-label="Rate changes" className="flex min-w-0 flex-[2_1_320px] flex-col gap-4">
          {!canChange ? (
            <section className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
              <h2 className="m-0 text-[17px] font-extrabold">You can see rates, not change them</h2>
              <p className="m-0 text-md leading-normal text-muted">
                Proposing and approving a rate change needs the rates change permission. Ask an admin if you need it.
              </p>
            </section>
          ) : (
            <>
              {book.waiting.length === 0 ? (
                <section className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
                  <h2 className="m-0 text-[17px] font-extrabold">Nothing waiting for approval</h2>
                  <p className="m-0 text-md leading-normal text-muted">Propose a change below. A second person approves it before it starts.</p>
                </section>
              ) : (
                book.waiting.map((proposal) => (
                  <Waiting
                    key={`${proposal.id}:${round}`}
                    proposal={proposal}
                    productName={names.get(proposal.productCode) ?? proposal.productCode}
                    current={book.products.find((product) => product.code === proposal.productCode)?.rate ?? null}
                    mine={proposal.proposedBy === me}
                    busy={busy === proposal.id}
                    error={failed?.on === proposal.id ? failed.message : null}
                    onApprove={() => void run(proposal.id, () => api.approve(proposal.id))}
                    onSendBack={(note) => void run(proposal.id, () => api.sendBack(proposal.id, note))}
                    onWithdraw={() => void run(proposal.id, () => api.withdraw(proposal.id))}
                  />
                ))
              )}
              <ProposeForm
                key={`propose:${round}`}
                products={book.products}
                today={day}
                busy={busy === "propose"}
                error={failed?.on === "propose" ? failed.message : null}
                onSubmit={(proposal) => void run("propose", () => api.propose(proposal))}
              />
            </>
          )}
        </aside>
      </div>
    </>
  );
}

type RateState = "in_force" | "starts" | "replaced";

function rateStates(history: readonly Rate[], today: string): RateState[] {
  // Newest start first, so the first one already started is the one in force.
  const inForce = history.findIndex((rate) => rate.effectiveFrom <= today);
  return history.map((rate, index) => (rate.effectiveFrom > today ? "starts" : index === inForce ? "in_force" : "replaced"));
}

const STATE_LABELS: Record<RateState, string> = { in_force: "In force", starts: "Approved, starts later", replaced: "Replaced" };
const STATE_STYLES: Record<RateState, string> = {
  in_force: "bg-success-surface text-success",
  starts: "bg-warning-surface text-warning-text",
  replaced: "bg-surface-sunken text-muted",
};

function History({ book, today }: { book: Book; today: string }) {
  const rows = book.products.flatMap((product) => {
    const history = book.history.get(product.code) ?? [];
    const states = rateStates(history, today);
    return history.map((rate, index) => ({ product, rate, state: states[index] ?? "replaced" }));
  });
  return (
    <section aria-labelledby="rate-history" className="flex flex-col gap-3">
      <h2 id="rate-history" className="m-0 text-[17px] font-extrabold">
        Rate history
      </h2>
      <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Rate history">
        <table className="w-full min-w-[620px] border-collapse text-md">
          <caption className="sr-only">Rate history</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {["Product", "Rate before tax", "Tax", "Clients see", "From", "Status"].map((heading) => (
                <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ product, rate, state }) => (
              <tr key={`${product.code}:${rate.effectiveFrom}`} className="border-t border-divider tabular-nums">
                <th scope="row" className="px-3 py-3 text-left font-bold">
                  {product.name}
                </th>
                <td className="px-3 py-3">{formatBps(rate.annualRateBps)}</td>
                <td className="px-3 py-3">{formatBps(rate.taxBps)}</td>
                <td className="px-3 py-3 font-bold">{rate.clientRatePercent}%</td>
                <td className="px-3 py-3 whitespace-nowrap">{formatDay(rate.effectiveFrom)}</td>
                <td className="px-3 py-3">
                  <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", STATE_STYLES[state])}>
                    {STATE_LABELS[state]}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="m-0 text-sm leading-[1.45] text-muted">
        A new rate applies to new investments only. A running investment keeps the rate it was quoted.
      </p>
    </section>
  );
}

interface WaitingProps {
  proposal: Proposal;
  productName: string;
  current: Rate | null;
  mine: boolean;
  busy: boolean;
  error: string | null;
  onApprove(): void;
  onSendBack(note: string): void;
  onWithdraw(): void;
}

function Waiting({ proposal, productName, current, mine, busy, error, onApprove, onSendBack, onWithdraw }: WaitingProps) {
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const noteId = `note-${proposal.id}`;
  const { rate } = proposal;
  return (
    <section
      aria-labelledby={`proposal-${proposal.id}`}
      className={cn("flex flex-col gap-3 rounded-xl bg-surface p-5", mine ? "border border-border" : "border-2 border-warning p-[19px]")}
    >
      <p className="m-0 text-xs font-bold text-muted">{mine ? "Waiting for a second person" : "Change waiting for you"}</p>
      <h2 id={`proposal-${proposal.id}`} className="m-0 text-[17px] font-extrabold">
        {productName}
      </h2>
      <dl className="m-0 flex flex-col gap-2.5">
        <Row name="Rate before tax" value={current ? `${formatBps(current.annualRateBps)} to ${formatBps(rate.annualRateBps)}` : formatBps(rate.annualRateBps)} />
        <Row name="Tax" value={formatBps(rate.taxBps)} />
        <Row name="Clients see" value={current ? `${current.clientRatePercent}% to ${rate.clientRatePercent}%` : `${rate.clientRatePercent}%`} />
        <Row name="Starts" value={formatDay(rate.effectiveFrom)} />
        <Row name="Proposed" value={`${mine ? "By you" : "By a colleague"}, ${formatStamp(proposal.proposedAt)}`} />
      </dl>
      <p className="m-0 rounded-lg bg-surface-sunken p-3 text-sm leading-[1.45]">{proposal.reason}</p>
      <p className="m-0 text-xs font-bold text-muted">New investments only</p>
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      {mine ? (
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

interface ProposeFormProps {
  products: readonly Product[];
  today: string;
  busy: boolean;
  error: string | null;
  onSubmit(proposal: NewProposal): void;
}

function ProposeForm({ products, today, busy, error, onSubmit }: ProposeFormProps) {
  const first = products[0];
  const [productCode, setProductCode] = useState(first?.code ?? "");
  const product = products.find((p) => p.code === productCode);
  const [rateText, setRateText] = useState(product?.rate ? formatBps(product.rate.annualRateBps).replace("%", "") : "");
  const [taxText, setTaxText] = useState(product?.rate ? formatBps(product.rate.taxBps).replace("%", "") : "");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);

  const annualRateBps = parsePercent(rateText);
  const taxBps = parsePercent(taxText);
  const rateProblem = annualRateBps === null || annualRateBps < 1 || annualRateBps > 5000 ? "Type a rate from 0.01 to 50, such as 12.5." : null;
  const taxProblem = taxBps === null ? "Type the tax as a percentage, such as 15." : null;
  const dateProblem = !effectiveFrom ? "Pick the day it starts." : effectiveFrom <= today ? "Pick a day after today." : null;
  const reasonProblem = reason.trim() ? null : "Say why the rate is changing.";

  function choose(code: string) {
    setProductCode(code);
    const next = products.find((p) => p.code === code);
    setRateText(next?.rate ? formatBps(next.rate.annualRateBps).replace("%", "") : "");
    setTaxText(next?.rate ? formatBps(next.rate.taxBps).replace("%", "") : "");
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (rateProblem || taxProblem || dateProblem || reasonProblem || annualRateBps === null || taxBps === null) return;
    onSubmit({ productCode, annualRateBps, taxBps, effectiveFrom, reason: reason.trim() });
  }

  const field = "flex flex-col gap-1.5";
  return (
    <form noValidate aria-labelledby="propose" onSubmit={submit} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 id="propose" className="m-0 text-[17px] font-extrabold">
        Propose a rate change
      </h2>
      <div className={field}>
        <Label htmlFor="propose-product">Product</Label>
        <select
          id="propose-product"
          value={productCode}
          onChange={(event) => choose(event.target.value)}
          className="h-[54px] w-full rounded-lg border border-border-strong bg-surface px-3 text-lg font-semibold"
        >
          {products.map((p) => (
            <option key={p.code} value={p.code}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Problem id="propose-rate" label="Rate before tax, %" problem={checked ? rateProblem : null}>
          <Input id="propose-rate" inputMode="decimal" value={rateText} onChange={(e) => setRateText(e.target.value)} aria-invalid={checked && !!rateProblem} />
        </Problem>
        <Problem id="propose-tax" label="Tax, %" problem={checked ? taxProblem : null}>
          <Input id="propose-tax" inputMode="decimal" value={taxText} onChange={(e) => setTaxText(e.target.value)} aria-invalid={checked && !!taxProblem} />
        </Problem>
      </div>
      <Problem id="propose-from" label="Starts on" problem={checked ? dateProblem : null}>
        <Input id="propose-from" type="date" min={today} value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} aria-invalid={checked && !!dateProblem} />
      </Problem>
      <Problem id="propose-reason" label="Reason, for the audit trail" problem={checked ? reasonProblem : null}>
        <textarea
          id="propose-reason"
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-invalid={checked && !!reasonProblem}
          className="w-full rounded-lg border border-border-strong bg-surface p-3 text-md focus-visible:border-black aria-invalid:border-danger"
        />
      </Problem>
      <div className="flex flex-col gap-1 rounded-lg bg-surface-sunken p-4">
        <p className="m-0 text-xs font-bold text-muted">What a client would see</p>
        <p className="m-0 text-[22px] font-extrabold tracking-[-0.4px] tabular-nums">
          {annualRateBps !== null && taxBps !== null && !rateProblem ? `${previewClientRate(annualRateBps, taxBps)} a year` : "Type a rate and tax"}
        </p>
        <p className="m-0 text-sm text-muted">
          After tax. New investments only{product?.rate ? `. Today it is ${product.rate.clientRatePercent}%` : ""}.
        </p>
      </div>
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      <Button type="submit" disabled={busy || products.length === 0}>
        Send for approval
      </Button>
    </form>
  );
}

function Problem({ id, label, problem, children }: { id: string; label: string; problem: string | null; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {problem ? <p className="m-0 text-sm font-bold text-danger">{problem}</p> : null}
    </div>
  );
}

function Row({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
      <dt className="text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold tabular-nums">{value}</dd>
    </div>
  );
}

export default function RatesPage() {
  const { session } = useAuth();
  // Sample rates under `pnpm dev` or a preview build, otherwise the real API. `?sample=empty` shows nothing waiting.
  const [api] = useState<CatalogueApi>(() =>
    isStubAuth() && session
      ? createStubCatalogueApi({
          me: session.staffId,
          today: kampalaToday(),
          sample: new URLSearchParams(window.location.search).get("sample") === "empty" ? "empty" : "default",
        })
      : connectedCatalogueApi(),
  );
  return (
    <RatesScreen api={api} me={session?.staffId ?? ""} canChange={session ? can(session, "rates.change") : false} />
  );
}
