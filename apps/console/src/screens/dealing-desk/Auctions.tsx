import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  AuctionError,
  auctionErrorMessage,
  type Auction,
  type AuctionCalendar,
  type AuctionsApi,
} from "./auctions.ts";
import { formatAuctionDay, formatDay, formatWhen } from "./format.ts";
import { FIELD, Field } from "./Orders.tsx";

type Load =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; calendar: AuctionCalendar };

export interface AuctionProduct {
  code: string;
  name: string;
}

/** A form that asks for words before a step: why a date is cancelled, or why a change is declined. */
type Asking = { kind: "cancel" | "decline"; id: string; title: string };

interface AuctionSectionProps {
  api: AuctionsApi;
  /** The Treasury bills on offer, the only products bought at auction. */
  products: readonly AuctionProduct[];
  /** The Kampala business day, as a calendar date. */
  today: string;
  newKey?: () => string;
  /** Called after a change is proposed or decided, so the desk can show the new next auction. */
  onChanged?: () => void;
}

const ADD = "add";

/** The Bank of Uganda's Treasury bill auction dates, each change proposed by one person and approved by another. */
export function AuctionSection({
  api,
  products,
  today,
  newKey = () => crypto.randomUUID(),
  onChanged,
}: AuctionSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(
    null,
  );
  const [asking, setAsking] = useState<Asking | null>(null);
  // A press again of the same command carries the same key, so a first try that reached Finvest is not applied twice.
  const pending = useRef<{ request: string; key: string } | null>(null);

  const fetchCalendar = useCallback(
    (): Promise<Load> =>
      api.getCalendar().then(
        (calendar) => ({ state: "ready", calendar }),
        (error: unknown) => ({
          state: "failed",
          message: auctionErrorMessage(error),
        }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchCalendar().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchCalendar]);

  function keyFor(request: string): string {
    if (pending.current?.request === request) return pending.current.key;
    const key = newKey();
    pending.current = { request, key };
    return key;
  }

  function forget() {
    pending.current = null;
    setFailed(null);
  }

  async function run(
    on: string,
    request: unknown,
    command: (key: string) => Promise<AuctionCalendar>,
  ): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      const calendar = await command(keyFor(JSON.stringify(request)));
      pending.current = null;
      setLoad({ state: "ready", calendar });
      onChanged?.();
      return true;
    } catch (error) {
      if (error instanceof AuctionError && error.code !== "unavailable")
        pending.current = null;
      setFailed({ on, message: auctionErrorMessage(error) });
      if (
        error instanceof AuctionError &&
        (error.code === "conflict" || error.code === "not_found")
      ) {
        const fresh = await fetchCalendar();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  const heading = (
    <h2 id="auction-heading" className="m-0 text-xl font-extrabold">
      Auction dates
    </h2>
  );

  if (load.state === "loading") {
    return (
      <section
        aria-labelledby="auction-heading"
        className="flex flex-col gap-3"
      >
        {heading}
        <p role="status" className="m-0 text-md text-muted">
          Loading the auction calendar
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section
        aria-labelledby="auction-heading"
        className="flex flex-col gap-3"
      >
        {heading}
        <div
          role="alert"
          className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5"
        >
          <h3 className="m-0 text-[17px] font-extrabold">
            The auction calendar could not be loaded
          </h3>
          <p className="m-0 text-md leading-normal text-muted">
            {load.message}
          </p>
          <Button
            variant="secondary"
            onClick={() => void fetchCalendar().then(setLoad)}
          >
            Try again
          </Button>
        </div>
      </section>
    );
  }

  const { calendar } = load;
  const name = (code: string) =>
    products.find((p) => p.code === code)?.name ?? code;
  // Anything still waiting for a decision stays in view, even once its day has passed.
  const open = (a: Auction) =>
    a.auctionDate >= today ||
    a.state === "waiting" ||
    a.cancellation?.status === "proposed";
  const coming = calendar.auctions.filter(open);
  const past = calendar.auctions.filter((a) => !open(a));

  function act(id: string, step: "approve" | "withdraw") {
    setAsking(null);
    void run(id, { id, step }, (key) =>
      step === "approve" ? api.approve(key, id) : api.withdraw(key, id),
    );
  }

  function ask(kind: Asking["kind"], id: string, title: string) {
    forget();
    setAsking({ kind, id, title });
  }

  return (
    <section aria-labelledby="auction-heading" className="flex flex-col gap-4">
      <div>
        {heading}
        <p className="m-0 text-md leading-normal text-muted">
          The Treasury bill auctions the Bank of Uganda publishes. One person
          proposes each date, and a different person approves it before the desk
          counts it. If the Bank moves one, propose cancelling it with the
          reason, and propose the new day.
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-4">
          {coming.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md leading-normal">
              No auction dates are in the calendar from today on. Until one is
              approved, the desk shows &quot;Not set&quot; for each bill&apos;s
              next auction.
            </p>
          ) : (
            <DateTable
              label="Auction dates from today on"
              auctions={coming}
              name={name}
              busy={busy}
              onAct={act}
              onAsk={ask}
            />
          )}
          {asking ? (
            <ReasonForm
              key={`${asking.kind}:${asking.id}`}
              asking={asking}
              busy={busy === asking.id}
              error={failed?.on === asking.id ? failed.message : null}
              onClose={() => {
                setAsking(null);
                forget();
              }}
              onSubmit={(words) =>
                void run(asking.id, { ...asking, words }, (key) =>
                  asking.kind === "cancel"
                    ? api.proposeCancel(key, asking.id, words)
                    : api.decline(key, asking.id, words),
                ).then((done) => done && setAsking(null))
              }
            />
          ) : null}
          {failed && failed.on !== ADD && failed.on !== asking?.id ? (
            <p
              role="alert"
              className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger"
            >
              {failed.message}
            </p>
          ) : null}
          {past.length > 0 ? (
            <details className="rounded-xl border border-border bg-surface p-5">
              <summary className="cursor-pointer text-md font-bold">
                Past month ({past.length})
              </summary>
              <div className="mt-3">
                <DateTable
                  label="Auction dates in the past month"
                  auctions={past}
                  name={name}
                  busy={null}
                />
              </div>
            </details>
          ) : null}
        </div>
        {calendar.mayChange ? (
          <ProposeForm
            products={products}
            today={today}
            busy={busy === ADD}
            error={failed?.on === ADD ? failed.message : null}
            onChange={() => {
              pending.current = null;
              setFailed((last) => (last?.on === ADD ? null : last));
            }}
            onSubmit={(productCode, auctionDate) =>
              run(ADD, { productCode, auctionDate }, (key) =>
                api.propose(key, productCode, auctionDate),
              )
            }
          />
        ) : null}
      </div>
    </section>
  );
}

interface DateTableProps {
  label: string;
  auctions: readonly Auction[];
  name: (code: string) => string;
  busy: string | null;
  onAct?: (changeId: string, step: "approve" | "withdraw") => void;
  onAsk?: (kind: Asking["kind"], changeId: string, title: string) => void;
}

function DateTable({
  label,
  auctions,
  name,
  busy,
  onAct,
  onAsk,
}: DateTableProps) {
  const actions = onAct !== undefined;
  // A list of cards rather than a table, so each date's buttons stay in reach on a phone.
  return (
    <div
      role="region"
      aria-label={label}
      className="rounded-xl border border-border bg-surface"
    >
      <ul aria-label={`${label}, earliest first`} className="m-0 list-none p-0">
        {auctions.map((auction) => {
          const gone =
            auction.state === "cancelled" ||
            auction.state === "declined" ||
            auction.state === "withdrawn";
          return (
            <li
              key={auction.id}
              className="flex flex-col gap-3 border-t border-divider px-4 py-3 text-sm first:border-t-0 sm:flex-row sm:items-start sm:justify-between"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <div className="flex flex-wrap gap-x-2">
                  <span
                    className={`font-bold whitespace-nowrap ${gone ? "text-muted line-through" : ""}`}
                  >
                    <span aria-hidden="true">
                      {formatAuctionDay(auction.auctionDate)}
                    </span>
                    <span className="sr-only">
                      {formatDay(auction.auctionDate)}
                    </span>
                  </span>
                  <span className="whitespace-nowrap">
                    {name(auction.productCode)}
                  </span>
                </div>
                <div>
                  <Status auction={auction} />
                </div>
                <div className="text-xs text-muted">
                  Proposed by {auction.proposedBy},{" "}
                  {formatWhen(auction.proposedAt)}
                </div>
              </div>
              {actions ? (
                <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
                  <Steps
                    auction={auction}
                    what={`the ${name(auction.productCode)} auction on ${formatDay(auction.auctionDate)}`}
                    busy={busy}
                    onAct={onAct}
                    onAsk={onAsk}
                  />
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The buttons for the steps the reader may take on a date and on its cancellation. */
function Steps(props: {
  auction: Auction;
  what: string;
  busy: string | null;
  onAct: DateTableProps["onAct"];
  onAsk: DateTableProps["onAsk"];
}) {
  const { auction, what, busy, onAct, onAsk } = props;
  const disabled = busy !== null;
  const cancellation = auction.cancellation;
  const button = (
    key: string,
    text: string,
    label: string,
    primary: boolean,
    onClick: () => void,
  ) => (
    <Button
      key={key}
      variant={primary ? undefined : "secondary"}
      className="whitespace-nowrap"
      disabled={disabled}
      aria-label={label}
      onClick={onClick}
    >
      {text}
    </Button>
  );
  const shown: ReactNode[] = [];
  for (const step of auction.allowed) {
    if (step === "approve")
      shown.push(
        button("approve", "Approve", `Approve ${what}`, true, () =>
          onAct?.(auction.id, "approve"),
        ),
      );
    if (step === "decline")
      shown.push(
        button("decline", "Decline", `Decline ${what}`, false, () =>
          onAsk?.("decline", auction.id, `Decline ${what}`),
        ),
      );
    if (step === "withdraw")
      shown.push(
        button("withdraw", "Withdraw", `Withdraw ${what}`, false, () =>
          onAct?.(auction.id, "withdraw"),
        ),
      );
    if (step === "cancel")
      shown.push(
        button("cancel", "Cancel date", `Cancel date for ${what}`, false, () =>
          onAsk?.("cancel", auction.id, `Cancel ${what}`),
        ),
      );
  }
  if (cancellation) {
    for (const step of cancellation.allowed) {
      if (step === "approve")
        shown.push(
          button(
            "c-approve",
            "Approve cancellation",
            `Approve cancelling ${what}`,
            true,
            () => onAct?.(cancellation.id, "approve"),
          ),
        );
      if (step === "decline")
        shown.push(
          button(
            "c-decline",
            "Decline",
            `Decline cancelling ${what}`,
            false,
            () =>
              onAsk?.("decline", cancellation.id, `Decline cancelling ${what}`),
          ),
        );
      if (step === "withdraw")
        shown.push(
          button(
            "c-withdraw",
            "Withdraw cancellation",
            `Withdraw cancelling ${what}`,
            false,
            () => onAct?.(cancellation.id, "withdraw"),
          ),
        );
    }
  }
  return <>{shown}</>;
}

function Status({ auction }: { auction: Auction }) {
  const c = auction.cancellation;
  const line = (main: ReactNode, detail?: string) => (
    <>
      <span className="font-bold">{main}</span>
      {detail ? (
        <span className="block text-xs text-muted">{detail}</span>
      ) : null}
    </>
  );
  const waiting = (text: string) => (
    <span className="text-warning-text">{text}</span>
  );
  switch (auction.state) {
    case "waiting":
      return line(waiting("Waiting for a second person"));
    case "standing":
      return line(
        "Standing",
        c?.status === "declined"
          ? `Approved by ${auction.decidedBy}. Cancelling it was declined: ${c.decisionNote}`
          : `Approved by ${auction.decidedBy}`,
      );
    case "cancelling":
      return line(
        waiting("Cancellation waiting for a second person"),
        `${c?.reason}. Asked by ${c?.proposedBy}`,
      );
    case "cancelled":
      return line(
        "Cancelled",
        `${c?.reason}. Asked by ${c?.proposedBy}, approved by ${c?.decidedBy}`,
      );
    case "declined":
      return line(
        "Declined",
        `By ${auction.decidedBy}: ${auction.decisionNote}`,
      );
    case "withdrawn":
      return line("Withdrawn", `By ${auction.decidedBy}`);
  }
}

function ReasonForm(props: {
  asking: Asking;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (words: string) => void;
}) {
  const { asking, busy, error, onClose, onSubmit } = props;
  const [words, setWords] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  const id = `${asking.kind}-${asking.id}`;
  const cancelling = asking.kind === "cancel";
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!words.trim()) {
      setTyped(
        auctionErrorMessage(
          new AuctionError(cancelling ? "reason_required" : "note_required"),
        ),
      );
      return;
    }
    setTyped(null);
    onSubmit(words.trim());
  }
  return (
    <form
      noValidate
      aria-labelledby={`${id}-title`}
      onSubmit={submit}
      className="flex flex-col gap-3 rounded-xl border-2 border-black bg-surface p-[19px]"
    >
      <h3 id={`${id}-title`} className="m-0 text-[17px] font-extrabold">
        {asking.title}
      </h3>
      <Field
        id={`${id}-words`}
        label={
          cancelling ? "Why is it cancelled?" : "Why are you declining it?"
        }
        error={typed ?? undefined}
        hint={
          cancelling
            ? "A different person approves the cancellation. Until then the date still counts."
            : "The person who proposed it sees this."
        }
      >
        {(control) => (
          <Input
            {...control}
            className={FIELD}
            maxLength={500}
            value={words}
            onChange={(e) => setWords(e.target.value)}
          />
        )}
      </Field>
      {error ? (
        <p
          role="alert"
          className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger"
        >
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {cancelling ? "Propose cancelling it" : "Decline it"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onClose}>
          Keep it
        </Button>
      </div>
    </form>
  );
}

interface ProposeFormProps {
  products: readonly AuctionProduct[];
  today: string;
  busy: boolean;
  error: string | null;
  onChange: () => void;
  onSubmit: (productCode: string, auctionDate: string) => Promise<boolean>;
}

function ProposeForm({
  products,
  today,
  busy,
  error,
  onChange,
  onSubmit,
}: ProposeFormProps) {
  const [productCode, setProductCode] = useState(products[0]?.code ?? "");
  const [auctionDate, setAuctionDate] = useState("");
  const [typed, setTyped] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(auctionDate)) {
      setTyped("Enter the day of the auction.");
      return;
    }
    if (auctionDate < today) {
      setTyped(auctionErrorMessage(new AuctionError("auction_date_passed")));
      return;
    }
    setTyped(null);
    if (await onSubmit(productCode, auctionDate)) setAuctionDate("");
  }

  if (products.length === 0) return null;
  return (
    <form
      noValidate
      aria-labelledby="propose-auction"
      onSubmit={(event) => void submit(event)}
      className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5"
    >
      <h3 id="propose-auction" className="m-0 text-[17px] font-extrabold">
        Propose an auction date
      </h3>
      <Field id="auction-product" label="Treasury bill">
        {(control) => (
          <select
            {...control}
            className={FIELD}
            value={productCode}
            onChange={(event) => {
              setProductCode(event.target.value);
              onChange();
            }}
          >
            {products.map((product) => (
              <option key={product.code} value={product.code}>
                {product.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field
        id="auction-date"
        label="Day of the auction"
        error={typed ?? undefined}
        hint="As the Bank of Uganda published it. A different person approves it."
      >
        {(control) => (
          <Input
            {...control}
            type="date"
            min={today}
            className={FIELD}
            value={auctionDate}
            onChange={(event) => {
              setAuctionDate(event.target.value);
              onChange();
            }}
          />
        )}
      </Field>
      {error ? (
        <p
          role="alert"
          className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger"
        >
          {error}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={busy}>
          Propose the date
        </Button>
      </div>
    </form>
  );
}
