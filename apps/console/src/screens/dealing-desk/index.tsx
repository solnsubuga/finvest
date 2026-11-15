import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { DealingError, dealingErrorMessage, notConnectedDealingApi, type DealingApi, type Desk, type NewOrder } from "./api.ts";
import { OrderCard, PrepareForm, type OrderCommand } from "./Orders.tsx";
import { BufferRules, Figures, PositionTable } from "./Position.tsx";
import { AllocationSection } from "./Allocations.tsx";
import { AuctionSection } from "./Auctions.tsx";
import { notConnectedAuctionsApi, type AuctionsApi } from "./auctions.ts";
import { createHttpAuctionsApi } from "./auctionsHttp.ts";
import { createStubAuctionsApi } from "./auctionsStub.ts";
import { notConnectedAllocationsApi, type AllocationsApi } from "./allocations.ts";
import { createHttpAllocationsApi } from "./allocationsHttp.ts";
import { createStubAllocationsApi } from "./allocationsStub.ts";
import { BondSalesSection } from "./BondSales.tsx";
import { notConnectedBondSalesApi, type BondSalesApi } from "./bondSales.ts";
import { createHttpBondSalesApi } from "./bondSalesHttp.ts";
import { createStubBondSalesApi } from "./bondSalesStub.ts";
import { BufferSection } from "./Buffer.tsx";
import { notConnectedBufferApi, type BufferApi } from "./buffer.ts";
import { createHttpBufferApi } from "./bufferHttp.ts";
import { BUFFER_COLLEAGUE, createStubBufferApi } from "./bufferStub.ts";
import { createHttpDealingApi } from "./dealingHttp.ts";
import { createStubDealingApi } from "./stub.ts";

const SUMMARY = "Turn client payments into real bills, and keep enough cash to pay people out.";
const NEW_ORDER = "new";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; desk: Desk };

interface DealingDeskScreenProps {
  api: DealingApi;
  /** Makes the idempotency key for one command. Every try at the same command carries the same key. */
  newKey?: () => string;
  /** The Kampala business day, as a calendar date. */
  today?: () => string;
  /** The auction calendar, shown under the position when given. */
  auctions?: AuctionsApi;
}

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

export function DealingDeskScreen({ api, newKey = () => crypto.randomUUID(), today = kampalaToday, auctions }: DealingDeskScreenProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [preparing, setPreparing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // Kept with the order it belongs to, so it never shows against another one.
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  // Bumped after each command, so a half-filled form on a card is dropped once the order moves on.
  const [round, setRound] = useState(0);
  // The key of a command that has not been answered yet, by the order it is for.
  // A second press sends the same key, so a first try that did reach the service is replayed, not applied twice.
  const pending = useRef(new Map<string, { request: string; key: string }>());

  function keyFor(on: string, request: string): string {
    const waiting = pending.current.get(on);
    if (waiting?.request === request) return waiting.key;
    const key = newKey();
    pending.current.set(on, { request, key });
    return key;
  }

  /** The person gave the command up, so the next one for this order is a new command. */
  function cancel(on: string) {
    pending.current.delete(on);
    setFailed((last) => (last?.on === on ? null : last));
  }

  const fetchDesk = useCallback(
    (): Promise<Load> =>
      api.getDesk().then(
        (desk) => ({ state: "ready", desk }),
        (error: unknown) => ({ state: "failed", message: dealingErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchDesk().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchDesk]);

  /**
   * Runs one command. `request` is everything the person asked for, written out,
   * so the key is kept while they press again and replaced once they change a value.
   */
  async function run(on: string, request: unknown, command: (key: string) => Promise<Desk>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      setLoad({ state: "ready", desk: await command(keyFor(on, JSON.stringify(request))) });
      pending.current.delete(on);
      setRound((count) => count + 1);
      return true;
    } catch (error) {
      // Only an answer that never arrived leaves the command open. A refusal is an answer.
      if (error instanceof DealingError && error.code !== "unavailable") pending.current.delete(on);
      setFailed({ on, message: dealingErrorMessage(error) });
      if (error instanceof DealingError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchDesk();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function prepare(order: NewOrder) {
    if (await run(NEW_ORDER, order, (key) => api.prepareOrder(key, order))) setPreparing(false);
  }

  function act(orderId: string, command: OrderCommand) {
    void run(orderId, command, (key) => {
      switch (command.action) {
        case "approve":
          return api.approve(key, orderId);
        case "withdraw":
          return api.withdraw(key, orderId);
        case "confirm_result":
          return api.confirmResult(key, orderId);
        case "send_back":
          return api.sendBack(key, orderId, command.note);
        case "reject_result":
          return api.rejectResult(key, orderId, command.note);
        case "record_result":
          return api.recordResult(key, orderId, command.fill);
      }
    });
  }

  if (load.state === "loading") {
    return (
      <>
        <PageHeader title="Dealing desk" summary={SUMMARY} />
        <p role="status" className="m-0 text-md text-muted">
          Loading the dealing desk
        </p>
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        <PageHeader title="Dealing desk" summary={SUMMARY} />
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">The dealing desk could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchDesk().then(setLoad)}>
            Try again
          </Button>
        </section>
      </>
    );
  }

  const { desk } = load;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Dealing desk" summary={SUMMARY} />
        <Button className="px-[18px]" disabled={preparing} onClick={() => setPreparing(true)}>
          Prepare an order
        </Button>
      </div>
      <Figures desk={desk} />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-4">
          <PositionTable desk={desk} />
          <BufferRules desk={desk} />
        </div>
        <aside aria-label="Orders" className="flex min-w-0 flex-[2_1_300px] flex-col gap-4">
          {preparing ? (
            <PrepareForm
              desk={desk}
              busy={busy === NEW_ORDER}
              error={failed?.on === NEW_ORDER ? failed.message : null}
              onCancel={() => {
                setPreparing(false);
                cancel(NEW_ORDER);
              }}
              onSubmit={(order) => void prepare(order)}
            />
          ) : null}
          {desk.orders.length === 0 && !preparing ? (
            <section className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
              <h2 className="m-0 text-[17px] font-extrabold">No open orders</h2>
              <p className="m-0 text-md leading-normal text-muted">
                Nothing is waiting for approval or for a result. Prepare an order when there is client money to place.
              </p>
            </section>
          ) : null}
          {desk.orders.map((order) => (
            <OrderCard
              key={`${order.id}:${order.status}:${round}`}
              order={order}
              position={desk.positions.find((position) => position.productCode === order.productCode)}
              busy={busy === order.id}
              error={failed?.on === order.id ? failed.message : null}
              today={today()}
              onCommand={(command) => act(order.id, command)}
              onCancel={() => cancel(order.id)}
            />
          ))}
        </aside>
      </div>
      {auctions ? (
        <AuctionSection
          api={auctions}
          products={desk.positions.filter((p) => p.discountOnly).map((p) => ({ code: p.productCode, name: p.productName }))}
          today={today()}
          newKey={newKey}
          // A new or cancelled date can change a bill's next auction, so the desk is read again.
          onChanged={() =>
            void fetchDesk().then((fresh) => {
              if (fresh.state === "ready") setLoad(fresh);
            })
          }
        />
      ) : null}
    </>
  );
}

export default function DealingDeskPage() {
  const { session } = useAuth();
  // Sample figures under `pnpm dev` or a preview build, like sign-in. Otherwise the real API.
  const [api] = useState<DealingApi>(() => {
    if (isStubAuth() && session) return createStubDealingApi({ id: session.staffId, name: session.name });
    const base = apiBaseUrl();
    if (!base) return notConnectedDealingApi;
    const http = createHttp(base);
    return createHttpDealingApi(http, createHttpDirectory(http));
  });
  // The buffer is on the real API. Under the stub, `?sample=empty` shows it before Finvest has paid any money in,
  // and `?sample=offline` as if Finvest could not be reached.
  const [buffer] = useState<{ api: BufferApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      const sample = new URLSearchParams(window.location.search).get("sample");
      const directory = createStubDirectory({ id: session.staffId, name: session.name });
      const names: Directory = {
        ...directory,
        staffName: async (id) => (id === BUFFER_COLLEAGUE.id ? BUFFER_COLLEAGUE.name : directory.staffName(id)),
      };
      if (sample === "offline") return { api: notConnectedBufferApi, directory: names };
      return { api: createStubBufferApi({ id: session.staffId }, sample === "empty" ? "empty" : "sample"), directory: names };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedBufferApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpBufferApi(http), directory: createHttpDirectory(http) };
  });
  // The auction calendar too, with an empty calendar and offline under the stub.
  const [auctions] = useState<AuctionsApi>(() => {
    if (isStubAuth() && session) {
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return notConnectedAuctionsApi;
      return createStubAuctionsApi({ name: session.name }, sample === "empty" ? "empty" : "sample", kampalaToday);
    }
    const base = apiBaseUrl();
    if (!base) return notConnectedAuctionsApi;
    const http = createHttp(base);
    return createHttpAuctionsApi(http, createHttpDirectory(http));
  });
  // The allocation report is on the real API too, with the same samples under the stub.
  const [allocations] = useState<AllocationsApi>(() => {
    if (isStubAuth() && session) {
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return notConnectedAllocationsApi;
      return createStubAllocationsApi(sample === "empty" ? "empty" : "sample");
    }
    const base = apiBaseUrl();
    return base ? createHttpAllocationsApi(createHttp(base)) : notConnectedAllocationsApi;
  });
  // Clients' bonds sold early, with the same samples under the stub.
  const [bondSales] = useState<BondSalesApi>(() => {
    if (isStubAuth() && session) {
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return notConnectedBondSalesApi;
      return createStubBondSalesApi({ id: session.staffId, name: session.name }, sample === "empty" ? "empty" : "sample");
    }
    const base = apiBaseUrl();
    if (!base) return notConnectedBondSalesApi;
    const http = createHttp(base);
    return createHttpBondSalesApi(http, createHttpDirectory(http));
  });
  return (
    <>
      <DealingDeskScreen api={api} auctions={auctions} />
      {session ? (
        <BufferSection
          api={buffer.api}
          directory={buffer.directory}
          me={session.staffId}
          canChange={can(session, "dealing_orders.change")}
          today={kampalaToday}
        />
      ) : null}
      {session ? <BondSalesSection api={bondSales} today={kampalaToday} /> : null}
      {session ? <AllocationSection api={allocations} /> : null}
    </>
  );
}
