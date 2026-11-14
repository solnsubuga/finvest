import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { parseAmount } from "@/screens/dealing-desk/format.ts";
import { formatExact, formatStamp, formatTerm } from "../../products/format.ts";
import type { AtMaturity } from "../api.ts";
import {
  AT_MATURITY_WORDS,
  BookingsError,
  bookingFailureText,
  bookingProblemText,
  bookingsErrorMessage,
  type BookableProduct,
  type Booking,
  type BookingProblem,
  type BookingsApi,
  type NewBooking,
} from "./api.ts";
import { UploadForm } from "./UploadForm.tsx";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const REASON_LIMIT = 500;
// Decided bookings stay in view for a while, so the person who booked one sees what became of it.
const DECIDED_SHOWN = 4;

const STATUS: Record<Booking["status"], { label: string; className: string }> = {
  proposed: { label: "Waiting for a second person", className: "bg-warning-surface text-warning-text" },
  approved: { label: "Approved, saving it", className: "bg-success-surface text-success" },
  done: { label: "Waiting for the client to pay", className: "bg-success-surface text-success" },
  failed: { label: "Could not be saved", className: "bg-danger-surface text-danger" },
  sent_back: { label: "Sent back", className: "bg-surface-sunken text-muted" },
  withdrawn: { label: "Withdrawn", className: "bg-surface-sunken text-muted" },
};

type Load =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; items: readonly Booking[]; products: readonly BookableProduct[] };
type Failure = { on: string; message: string; problems: readonly BookingProblem[] };

interface BookingsProps {
  api: BookingsApi;
  directory: Directory;
  /** The signed-in member of staff. */
  me: string;
  /** `investment_corrections.change`: book and decide. The API checks it again. */
  canChange: boolean;
  newKey?: () => string;
}

/** Booking an investment a client asked for. One person books it, another approves. */
export function Bookings({ api, directory, me, canChange, newKey = () => crypto.randomUUID() }: BookingsProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [writing, setWriting] = useState<"one" | "sheet" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<Failure | null>(null);
  const [staffNames, setStaffNames] = useState<Record<string, string | null>>({});
  const [clients, setClients] = useState<Record<string, ClientCard | null>>({});
  const asked = useRef(new Set<string>());
  const pending = useRef(new Map<string, { request: string; key: string }>());

  const fetchList = useCallback(
    (): Promise<Load> =>
      Promise.all([api.list(), api.products()]).then(
        ([items, products]) => {
          for (const item of items) {
            for (const staffId of [item.proposedBy, ...(item.decidedBy ? [item.decidedBy] : [])]) {
              if (staffId === me || asked.current.has(staffId)) continue;
              asked.current.add(staffId);
              void directory.staffName(staffId).then(
                (name) => setStaffNames((known) => ({ ...known, [staffId]: name })),
                () => setStaffNames((known) => ({ ...known, [staffId]: null })),
              );
            }
            if (!asked.current.has(item.clientId)) {
              asked.current.add(item.clientId);
              void directory.client(item.clientId).then(
                (card) => setClients((known) => ({ ...known, [item.clientId]: card })),
                () => setClients((known) => ({ ...known, [item.clientId]: null })),
              );
            }
          }
          return { state: "ready", items, products };
        },
        (error: unknown) => ({ state: "failed", message: bookingsErrorMessage(error) }),
      ),
    [api, directory, me],
  );

  useEffect(() => {
    let current = true;
    void fetchList().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchList]);

  function keyFor(on: string, request: string): string {
    const waiting = pending.current.get(on);
    if (waiting?.request === request) return waiting.key;
    const key = newKey();
    pending.current.set(on, { request, key });
    return key;
  }

  async function run(on: string, request: unknown, command: (key: string) => Promise<Booking>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      await command(keyFor(on, JSON.stringify(request)));
      pending.current.delete(on);
      return true;
    } catch (error) {
      // Only an unanswered request keeps its key, so trying again repeats the same one.
      if (!(error instanceof BookingsError && error.code === "unavailable")) pending.current.delete(on);
      setFailed({ on, message: bookingsErrorMessage(error), problems: error instanceof BookingsError ? error.problems : [] });
      return false;
    } finally {
      const fresh = await fetchList();
      if (fresh.state === "ready") setLoad(fresh);
      setBusy(null);
    }
  }

  const who = (staffId: string) => (staffId === me ? "you" : (staffNames[staffId] ?? "a member of staff"));
  const clientName = (clientId: string) => clients[clientId]?.name ?? "the client";
  const items = load.state === "ready" ? load.items : [];
  const open = items.filter((item) => item.status === "proposed");
  const shown = [...open, ...items.filter((item) => item.status !== "proposed").slice(0, DECIDED_SHOWN)];

  return (
    <section aria-labelledby="book-for-client" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex max-w-[640px] flex-col gap-1">
          <h2 id="book-for-client" className="m-0 text-[17px] font-extrabold">
            Book for a client
          </h2>
          <p className="m-0 text-sm leading-normal text-muted">
            When a client asks us to set up an investment for them. One person books it, someone else approves. Finvest then saves it on the day&apos;s
            rate, the client is told, and they pay for it in the app as usual within 3 days.
          </p>
        </div>
        {canChange && !writing && load.state === "ready" ? (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setWriting("one");
                setFailed(null);
              }}
            >
              Book an investment
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setWriting("sheet");
                setFailed(null);
              }}
            >
              Upload a spreadsheet
            </Button>
          </div>
        ) : null}
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading bookings
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-2">
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchList().then(setLoad);
            }}
          >
            Try again
          </Button>
        </div>
      ) : (
        <>
          {writing === "sheet" ? (
            <UploadForm
              directory={directory}
              products={load.products}
              newKey={newKey}
              onBook={async (input, key) => {
                try {
                  await api.propose(key, input);
                  return null;
                } catch (error) {
                  return error instanceof BookingsError ? error : new BookingsError("unavailable");
                }
              }}
              onDone={async () => {
                const fresh = await fetchList();
                if (fresh.state === "ready") setLoad(fresh);
              }}
              onClose={() => setWriting(null)}
            />
          ) : null}
          {writing === "one" ? (
            <BookForm
              directory={directory}
              products={load.products}
              busy={busy === "new"}
              error={failed?.on === "new" ? failed.message : null}
              refused={failed?.on === "new" ? failed.problems : []}
              onCancel={() => {
                setWriting(null);
                setFailed(null);
                pending.current.delete("new");
              }}
              onSubmit={(input) =>
                run("new", input, (key) => api.propose(key, input)).then((done) => {
                  if (done) setWriting(null);
                  return done;
                })
              }
            />
          ) : null}
          {shown.length === 0 && !writing ? <p className="m-0 text-md text-muted">Nothing has been booked for a client yet.</p> : null}
          {shown.map((item) => (
            <BookingCard
              key={`${item.id}:${item.status}`}
              item={item}
              client={clientName(item.clientId)}
              mine={item.proposedBy === me}
              canChange={canChange}
              who={who}
              busy={busy === item.id}
              locked={busy !== null}
              error={failed?.on === item.id ? failed.message : null}
              onApprove={() => run(item.id, "approve", (key) => api.approve(key, item.id))}
              onSendBack={(note) => run(item.id, { note }, (key) => api.sendBack(key, item.id, note))}
              onWithdraw={() => run(item.id, "withdraw", (key) => api.withdraw(key, item.id))}
            />
          ))}
        </>
      )}
    </section>
  );
}

interface BookFormProps {
  directory: Directory;
  products: readonly BookableProduct[];
  busy: boolean;
  error: string | null;
  refused: readonly BookingProblem[];
  onCancel: () => void;
  onSubmit: (input: NewBooking) => Promise<boolean>;
}

function BookForm({ directory, products, busy, error, refused, onCancel, onSubmit }: BookFormProps) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ClientCard[]>([]);
  const [client, setClient] = useState<ClientCard | null>(null);
  const [productCode, setProductCode] = useState("");
  const [amountText, setAmountText] = useState("");
  const [atMaturity, setAtMaturity] = useState<AtMaturity>("roll_over");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);

  useEffect(() => {
    let current = true;
    void directory.findClients(client ? "" : query).then(
      (clients) => current && setFound(clients.filter((card) => card.verified && card.status === "active")),
      () => current && setFound([]),
    );
    return () => {
      current = false;
    };
  }, [directory, query, client]);

  const product = products.find((p) => p.code === productCode) ?? null;
  const amount = product && amountText.trim() ? parseAmount(amountText, product.currency) : null;
  const missing: BookingProblem[] = [
    ...(client ? [] : [{ field: "client_id" as const, code: "required" }]),
    ...(product ? [] : [{ field: "product_code" as const, code: "required" }]),
    ...(amountText.trim() ? [] : [{ field: "amount" as const, code: "required" }]),
    ...(amountText.trim() && product && amount === null ? [{ field: "amount" as const, code: "not_an_amount" }] : []),
    ...(amount && product && amount.minor < product.minimum.minor ? [{ field: "amount" as const, code: "below_minimum" }] : []),
    ...(reason.trim() ? [] : [{ field: "reason" as const, code: "required" }]),
    ...(reason.length > REASON_LIMIT ? [{ field: "reason" as const, code: "too_long" }] : []),
  ];
  const shown = [...(tried ? missing : missing.filter((p) => p.code !== "required")), ...refused];
  const codes = (field: BookingProblem["field"]) => [...new Set(shown.filter((p) => p.field === field).map((p) => p.code))];
  const text = (code: string) => (code === "not_an_amount" ? "Write the amount in whole units, like 1,000,000." : bookingProblemText(code));
  const errors = (field: BookingProblem["field"]) =>
    codes(field).map((code) => (
      <span key={code} className="text-xs font-bold text-danger">
        {text(code)}
      </span>
    ));

  return (
    <form
      aria-label="Book an investment for a client"
      className="flex flex-col gap-3 border-t border-divider pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (missing.length > 0 || !client || !product || !amount) return;
        void onSubmit({
          clientId: client.clientId,
          productCode: product.code,
          amount,
          atMaturity: product.termDays === null ? null : atMaturity,
          reason: reason.trim(),
        });
      }}
    >
      {client ? (
        <div className="flex items-center justify-between gap-2 rounded-lg bg-paper px-3.5 py-3 text-sm">
          <span>
            <strong>{client.name}</strong>
            <span className="text-muted">{client.accountNumber ? `, account ${client.accountNumber}` : ""}</span>
          </span>
          <Button variant="secondary" className="h-9 px-3" onClick={() => setClient(null)}>
            Change
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            Client
            <span className="text-xs font-medium text-muted">Only clients who passed their identity check.</span>
            <Input
              value={query}
              placeholder="Name or account number"
              aria-invalid={codes("client_id").length > 0}
              onChange={(event) => setQuery(event.target.value)}
              className="h-11 text-md"
            />
          </label>
          {found.length > 0 ? (
            <ul aria-label="Matching clients" className="m-0 flex list-none flex-col gap-1 p-0">
              {found.map((card) => (
                <li key={card.clientId}>
                  <button
                    type="button"
                    onClick={() => setClient(card)}
                    className="w-full cursor-pointer rounded-md px-3 py-2 text-left text-sm hover:bg-paper"
                  >
                    <strong>{card.name}</strong>
                    <span className="text-muted">{card.accountNumber ? `, ${card.accountNumber}` : ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : query.trim().length >= 2 ? (
            <p className="m-0 text-xs text-muted">No approved client matches that.</p>
          ) : null}
          {errors("client_id")}
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        <label className="flex min-w-[200px] flex-1 flex-col gap-1.5 text-sm font-bold">
          Product
          <select value={productCode} aria-invalid={codes("product_code").length > 0} onChange={(event) => setProductCode(event.target.value)} className={INPUT}>
            <option value="" disabled>
              Choose
            </option>
            {products.map((option) => (
              <option key={option.code} value={option.code}>
                {option.name}, {formatTerm(option.termDays).toLowerCase()}
              </option>
            ))}
          </select>
          {product ? <span className="text-xs font-medium text-muted">At least {formatExact(product.minimum)}.</span> : null}
          {errors("product_code")}
        </label>
        <label className="flex min-w-[200px] flex-1 flex-col gap-1.5 text-sm font-bold">
          Amount{product ? ` in ${product.currency}` : ""}
          <span className="text-xs font-medium text-muted">What the client will pay. There is no balance to take it from.</span>
          <input
            inputMode="numeric"
            value={amountText}
            placeholder="1,000,000"
            aria-invalid={codes("amount").length > 0}
            onChange={(event) => setAmountText(event.target.value)}
            className={INPUT}
          />
          {errors("amount")}
        </label>
      </div>
      {product && product.termDays !== null ? (
        <label className="flex max-w-[420px] flex-col gap-1.5 text-sm font-bold">
          When the term ends
          <span className="text-xs font-medium text-muted">To be paid, the client names their account in the app. They can change this until it matures.</span>
          <select value={atMaturity} onChange={(event) => setAtMaturity(event.target.value as AtMaturity)} className={INPUT}>
            {(Object.keys(AT_MATURITY_WORDS) as AtMaturity[]).map((option) => (
              <option key={option} value={option}>
                {AT_MATURITY_WORDS[option]}
              </option>
            ))}
          </select>
          {errors("at_maturity")}
        </label>
      ) : null}
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        <span className="flex justify-between gap-2">
          How did the client ask?
          <span className={cn("text-xs font-semibold", reason.length > REASON_LIMIT ? "text-danger" : "text-muted")}>
            {reason.length} of {REASON_LIMIT}
          </span>
        </span>
        <span className="text-xs font-medium text-muted">For the person who approves it and the audit trail: when and how they asked, and what for.</span>
        <textarea rows={2} value={reason} aria-invalid={codes("reason").length > 0} onChange={(event) => setReason(event.target.value)} className={INPUT} />
        {errors("reason")}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving" : "Send for approval"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

interface BookingCardProps {
  item: Booking;
  client: string;
  mine: boolean;
  canChange: boolean;
  who: (staffId: string) => string;
  busy: boolean;
  locked: boolean;
  error: string | null;
  onApprove: () => Promise<boolean>;
  onSendBack: (note: string) => Promise<boolean>;
  onWithdraw: () => Promise<boolean>;
}

function BookingCard({ item, client, mine, canChange, who, busy, locked, error, onApprove, onSendBack, onWithdraw }: BookingCardProps) {
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const status = STATUS[item.status];
  const waiting = item.status === "proposed";
  const title = `${formatExact(item.amount)} in the ${item.productName} for ${client}`;
  const decided = item.status === "sent_back" ? "Sent back by " : item.status === "withdrawn" ? "Withdrawn by " : "Approved by ";

  return (
    <article aria-label={title} className="flex flex-col gap-2 border-t border-divider pt-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-md font-extrabold">{title}</h3>
        <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", status.className)}>{status.label}</span>
      </div>
      <dl className="m-0 flex flex-col gap-1 text-sm text-muted">
        {item.atMaturity ? (
          <div>
            <dt className="inline">When the term ends: </dt>
            <dd className="m-0 inline text-ink">{AT_MATURITY_WORDS[item.atMaturity]}</dd>
          </div>
        ) : null}
        <div>
          <dt className="inline">Booked by </dt>
          <dd className="m-0 inline">
            {who(item.proposedBy)}, {formatStamp(item.proposedAt)}. <span className="text-ink">{item.reason}</span>
          </dd>
        </div>
        {item.decidedBy && item.decidedAt ? (
          <div>
            <dt className="inline">{decided}</dt>
            <dd className="m-0 inline">
              {who(item.decidedBy)}, {formatStamp(item.decidedAt)}
              {item.decisionNote ? (
                <>
                  . <span className="text-ink">{item.decisionNote}</span>
                </>
              ) : null}
            </dd>
          </div>
        ) : null}
      </dl>
      {item.status === "done" && item.investmentId ? (
        <p className="m-0 text-sm">Saved and the client was told. It is in the list above as awaiting payment until they pay.</p>
      ) : null}
      {item.status === "failed" ? <p className="m-0 text-sm font-semibold text-danger">{bookingFailureText(item.failureCode)} Nothing was saved. Book it again if the client still wants it.</p> : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {waiting && !canChange ? <p className="m-0 text-sm text-muted">Someone who can correct investments approves it.</p> : null}
      {waiting && canChange && mine ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted">Someone else approves it.</span>
          <Button variant="secondary" disabled={locked} onClick={() => void onWithdraw()}>
            {busy ? "Saving" : "Withdraw it"}
          </Button>
        </div>
      ) : null}
      {waiting && canChange && !mine && !sending ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-sm text-muted">Approving saves it for the client to pay. Check the client asked for exactly this.</p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={locked} onClick={() => void onApprove()}>
              {busy ? "Saving" : "Approve"}
            </Button>
            <Button variant="secondary" disabled={locked} onClick={() => setSending(true)}>
              Send back
            </Button>
          </div>
        </div>
      ) : null}
      {waiting && sending ? (
        <form
          aria-label="Send the booking back"
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (note.trim().length === 0) return;
            void onSendBack(note.trim()).then((done) => done && setSending(false));
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            Why send it back?
            <textarea rows={2} value={note} aria-invalid={tried && !note.trim()} onChange={(event) => setNote(event.target.value)} className={INPUT} />
            {tried && !note.trim() ? <span className="text-xs font-bold text-danger">{bookingProblemText("required")}</span> : null}
          </label>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={locked}>
              {busy ? "Saving" : "Send back"}
            </Button>
            <Button variant="secondary" disabled={locked} onClick={() => setSending(false)}>
              Keep it
            </Button>
          </div>
        </form>
      ) : null}
    </article>
  );
}
