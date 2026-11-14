import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
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

interface AuctionSectionProps {
  api: AuctionsApi;
  /** The Treasury bills on offer, the only products bought at auction. */
  products: readonly AuctionProduct[];
  /** The Kampala business day, as a calendar date. */
  today: string;
  newKey?: () => string;
  /** Called after a date is entered or cancelled, so the desk can show the new next auction. */
  onChanged?: () => void;
}

/** The Bank of Uganda's Treasury bill auction dates, as staff entered them. */
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
  const [cancelling, setCancelling] = useState<string | null>(null);
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
  const coming = calendar.auctions.filter((a) => a.auctionDate >= today);
  const past = calendar.auctions.filter((a) => a.auctionDate < today);

  return (
    <section aria-labelledby="auction-heading" className="flex flex-col gap-4">
      <div>
        {heading}
        <p className="m-0 text-md leading-normal text-muted">
          The Treasury bill auctions the Bank of Uganda publishes. The desk
          shows each bill&apos;s next date. If the Bank moves one, cancel it
          with the reason and enter the new day.
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-4">
          {coming.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md leading-normal">
              No auction dates are entered from today on. Until one is, the desk
              shows &quot;Not set&quot; for each bill&apos;s next auction.
            </p>
          ) : (
            <DateTable
              label="Auction dates from today on"
              auctions={coming}
              name={name}
              canCancel={calendar.mayChange}
              cancelling={cancelling}
              busy={busy}
              error={failed && failed.on !== "add" ? failed : null}
              onOpen={(id) => {
                setCancelling(id);
                setFailed(null);
                pending.current = null;
              }}
              onClose={() => {
                setCancelling(null);
                setFailed(null);
                pending.current = null;
              }}
              onCancel={(id, reason) =>
                void run(id, { id, reason }, (key) =>
                  api.cancel(key, id, reason),
                ).then((done) => done && setCancelling(null))
              }
            />
          )}
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
                  canCancel={false}
                  cancelling={null}
                  busy={null}
                  error={null}
                />
              </div>
            </details>
          ) : null}
        </div>
        {calendar.mayChange ? (
          <AddForm
            products={products}
            today={today}
            busy={busy === "add"}
            error={failed?.on === "add" ? failed.message : null}
            onChange={() => {
              pending.current = null;
              setFailed((last) => (last?.on === "add" ? null : last));
            }}
            onSubmit={(productCode, auctionDate) =>
              run("add", { productCode, auctionDate }, (key) =>
                api.add(key, productCode, auctionDate),
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
  canCancel: boolean;
  cancelling: string | null;
  busy: string | null;
  error: { on: string; message: string } | null;
  onOpen?: (id: string) => void;
  onClose?: () => void;
  onCancel?: (id: string, reason: string) => void;
}

function DateTable({
  label,
  auctions,
  name,
  canCancel,
  cancelling,
  busy,
  error,
  onOpen,
  onClose,
  onCancel,
}: DateTableProps) {
  const columns = ["Day", "Bill", "Status", "Entered by"];
  // The cancel form sits under the table, so it has the full width on a phone.
  const open = canCancel
    ? auctions.find((a) => a.id === cancelling)
    : undefined;
  return (
    <div className="flex flex-col gap-3">
      <div
        className="relative overflow-x-auto rounded-xl border border-border bg-surface"
        tabIndex={0}
        role="region"
        aria-label={label}
      >
        <table className="w-full min-w-[560px] border-collapse text-sm">
          <caption className="sr-only">{label}, earliest first</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {columns.map((column) => (
                <th key={column} scope="col" className="px-3 py-2 font-bold">
                  {column}
                </th>
              ))}
              {canCancel ? (
                <th scope="col" className="px-3 py-2">
                  <span className="sr-only">Actions</span>
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {auctions.flatMap((auction) => {
              const standing = auction.cancelledBy === null;
              const rows = [
                <tr
                  key={auction.id}
                  className="border-t border-divider align-top"
                >
                  <th
                    scope="row"
                    className={`px-3 py-3 text-left font-bold whitespace-nowrap ${standing ? "" : "text-muted line-through"}`}
                  >
                    <span title={formatDay(auction.auctionDate)}>
                      {formatAuctionDay(auction.auctionDate)}
                    </span>
                    <span className="sr-only">
                      , {formatDay(auction.auctionDate)}
                    </span>
                  </th>
                  <td className="px-3 py-3 whitespace-nowrap">
                    {name(auction.productCode)}
                  </td>
                  <td className="px-3 py-3">
                    {standing ? (
                      "Standing"
                    ) : (
                      <span className="text-muted">
                        Cancelled by {auction.cancelledBy}
                        {auction.cancelledAt
                          ? `, ${formatWhen(auction.cancelledAt)}`
                          : ""}
                        . {auction.cancelReason}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    {auction.addedBy}
                    <span className="sr-only">, </span>
                    <span className="block text-xs text-muted">
                      {formatWhen(auction.addedAt)}
                    </span>
                  </td>
                  {canCancel ? (
                    <td className="px-3 py-3 text-right">
                      {standing && cancelling !== auction.id ? (
                        <Button
                          variant="secondary"
                          className="whitespace-nowrap"
                          disabled={busy !== null}
                          aria-label={`Cancel date for ${name(auction.productCode)} on ${formatDay(auction.auctionDate)}`}
                          onClick={() => onOpen?.(auction.id)}
                        >
                          Cancel date
                        </Button>
                      ) : null}
                    </td>
                  ) : null}
                </tr>,
              ];
              return rows;
            })}
          </tbody>
        </table>
      </div>
      {open ? (
        <CancelForm
          key={open.id}
          auction={open}
          name={name(open.productCode)}
          busy={busy === open.id}
          error={error?.on === open.id ? error.message : null}
          onClose={() => onClose?.()}
          onSubmit={(reason) => onCancel?.(open.id, reason)}
        />
      ) : null}
      {error && error.on !== cancelling ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error.message}
        </p>
      ) : null}
    </div>
  );
}

function CancelForm(props: {
  auction: Auction;
  name: string;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (reason: string) => void;
}) {
  const { auction, name, busy, error, onClose, onSubmit } = props;
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  const id = `cancel-${auction.id}`;
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!reason.trim()) {
      setTyped(auctionErrorMessage(new AuctionError("reason_required")));
      return;
    }
    setTyped(null);
    onSubmit(reason.trim());
  }
  return (
    <form
      noValidate
      aria-labelledby={`${id}-title`}
      onSubmit={submit}
      className="flex flex-col gap-3 rounded-lg border-2 border-black bg-surface p-4 whitespace-normal"
    >
      <h3 id={`${id}-title`} className="m-0 text-md font-extrabold">
        Cancel the {name} auction on {formatDay(auction.auctionDate)}
      </h3>
      <Field
        id={`${id}-reason`}
        label="Why is it cancelled?"
        error={typed ?? undefined}
        hint="Kept with the date in the calendar and the audit trail."
      >
        {(control) => (
          <Input
            {...control}
            className={FIELD}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
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
          Cancel the date
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onClose}>
          Keep it
        </Button>
      </div>
    </form>
  );
}

interface AddFormProps {
  products: readonly AuctionProduct[];
  today: string;
  busy: boolean;
  error: string | null;
  onChange: () => void;
  onSubmit: (productCode: string, auctionDate: string) => Promise<boolean>;
}

function AddForm({
  products,
  today,
  busy,
  error,
  onChange,
  onSubmit,
}: AddFormProps) {
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
      aria-labelledby="add-auction"
      onSubmit={(event) => void submit(event)}
      className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5"
    >
      <h3 id="add-auction" className="m-0 text-[17px] font-extrabold">
        Enter an auction date
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
        hint="As the Bank of Uganda published it."
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
          Add the date
        </Button>
      </div>
    </form>
  );
}
