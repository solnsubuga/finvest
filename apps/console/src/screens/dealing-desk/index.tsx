import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { DealingError, dealingErrorMessage, notConnectedDealingApi, type DealingApi, type Desk, type NewOrder } from "./api.ts";
import { OrderCard, PrepareForm, type OrderCommand } from "./Orders.tsx";
import { BufferRules, Figures, PositionTable } from "./Position.tsx";
import { createStubDealingApi } from "./stub.ts";

const SUMMARY = "Turn client payments into real bills, and keep enough cash to pay people out.";
const NEW_ORDER = "new";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; desk: Desk };

interface DealingDeskScreenProps {
  api: DealingApi;
  /** Makes the idempotency key for one attempt at a command. */
  newKey?: () => string;
  /** The Kampala business day, as a calendar date. */
  today?: () => string;
}

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

export function DealingDeskScreen({ api, newKey = () => crypto.randomUUID(), today = kampalaToday }: DealingDeskScreenProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [preparing, setPreparing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // Kept with the order it belongs to, so it never shows against another one.
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  // Bumped after each command, so a half-filled form on a card is dropped once the order moves on.
  const [round, setRound] = useState(0);

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

  /** Runs one command. The key is made once here, so a retry inside the client reuses it. */
  async function run(on: string, command: (key: string) => Promise<Desk>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      setLoad({ state: "ready", desk: await command(newKey()) });
      setRound((count) => count + 1);
      return true;
    } catch (error) {
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
    if (await run(NEW_ORDER, (key) => api.prepareOrder(key, order))) setPreparing(false);
  }

  function act(orderId: string, command: OrderCommand) {
    void run(orderId, (key) => {
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
                setFailed(null);
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
            />
          ))}
        </aside>
      </div>
    </>
  );
}

export default function DealingDeskPage() {
  const { session } = useAuth();
  // Sample figures under `pnpm dev` or a preview build, like sign-in. Otherwise nothing until the API client exists.
  const [api] = useState<DealingApi>(() =>
    isStubAuth() && session ? createStubDealingApi({ id: session.staffId, name: session.name }) : notConnectedDealingApi,
  );
  return <DealingDeskScreen api={api} />;
}
