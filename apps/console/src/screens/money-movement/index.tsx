import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { formatExact, formatMoment } from "@/screens/reconciliation/format.ts";
import {
  PayoutsError,
  notConnectedPayoutsApi,
  payoutsErrorMessage,
  type HeldUpPayout,
  type PayoutsApi,
  type StrandedPayout,
} from "./api.ts";
import { createHttpPayoutsApi } from "./http.ts";
import { Settle, type SettleCommand } from "./Settle.tsx";
import { createStubPayoutsApi, type StubCase } from "./stub.ts";
import { notConnectedBankPayoutsApi, type BankPayoutsApi } from "./bank/api.ts";
import { BankPayouts } from "./bank/BankPayouts.tsx";
import { createHttpBankPayoutsApi } from "./bank/http.ts";
import { createStubBankPayoutsApi } from "./bank/stub.ts";
import { notConnectedReturnsApi, type ReturnsApi } from "./returns/api.ts";
import { createHttpReturnsApi } from "./returns/http.ts";
import { Returns } from "./returns/Returns.tsx";
import { createStubReturnsApi } from "./returns/stub.ts";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

const SUMMARY = "Payouts that need a person: those ioTec is not taking, and those it never answered for.";

interface MoneyMovementScreenProps {
  api: PayoutsApi;
  /** The signed-in member of staff. */
  me: string;
  /** Whether this person holds `reconciliation.change`, which settling a payout needs. */
  canSettle: boolean;
  /** Makes the idempotency key for one command. Every try at the same command carries the same key. */
  newKey?: () => string;
}

function useList<T>(fetch: () => Promise<T[]>, enabled = true) {
  const [load, setLoad] = useState<Load<T[]>>({ state: "loading" });
  const run = useCallback(
    (): Promise<Load<T[]>> =>
      fetch().then(
        (value): Load<T[]> => ({ state: "ready", value }),
        (error: unknown): Load<T[]> => ({ state: "failed", message: payoutsErrorMessage(error) }),
      ),
    [fetch],
  );
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    void run().then((result) => current && setLoad(result));
    return () => {
      current = false;
    };
  }, [run, enabled]);
  const reload = useCallback(async () => setLoad(await run()), [run]);
  return [load, reload] as const;
}

export function MoneyMovementScreen({ api, me, canSettle, newKey = () => crypto.randomUUID() }: MoneyMovementScreenProps) {
  const [heldUp, reloadHeldUp] = useList(useCallback(() => api.listHeldUp(), [api]));
  const [stranded, reloadStranded] = useList(
    useCallback(() => api.listStranded(), [api]),
    canSettle,
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  const [done, setDone] = useState<string | null>(null);
  // The key of a command not answered yet. Pressing it again sends the same key, so a first try
  // that did reach Finvest is replayed, not applied twice.
  const pending = useRef<{ request: string; key: string } | null>(null);

  const items = stranded.state === "ready" ? stranded.value : null;
  const current = items?.find((item) => item.id === selected) ?? items?.[0] ?? null;

  function keyFor(request: string): string {
    if (pending.current?.request === request) return pending.current.key;
    const key = newKey();
    pending.current = { request, key };
    return key;
  }

  async function run(payout: StrandedPayout, command: SettleCommand) {
    setBusy(true);
    setFailed(null);
    setDone(null);
    const key = keyFor(JSON.stringify({ id: payout.id, resolution: payout.resolution?.id, command }));
    const resolutionId = payout.resolution?.id ?? "";
    try {
      if (command.action === "propose") {
        await api.propose(key, payout.id, {
          outcome: command.outcome,
          ...(command.outcome === "paid" ? { statementLineId: command.evidence } : { statementId: command.evidence }),
          reason: command.reason,
        });
      } else if (command.action === "approve") {
        await api.approve(key, resolutionId, command.reason);
      } else if (command.action === "decline") {
        await api.decline(resolutionId, command.reason);
      } else {
        await api.withdraw(resolutionId, command.reason);
      }
      pending.current = null;
      setDone(DONE[command.action]);
      await reloadStranded();
    } catch (error) {
      // Only a definite refusal closes the command. An answer that never arrived is an unknown
      // outcome, so the next try reuses the same key.
      if (error instanceof PayoutsError && error.code !== "unavailable") pending.current = null;
      setFailed({ id: payout.id, message: payoutsErrorMessage(error) });
      if (error instanceof PayoutsError && REFRESH.has(error.code)) await reloadStranded();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Money movement" summary={SUMMARY} />

      <section aria-labelledby="held-up" className="flex flex-col gap-3">
        <div>
          <h2 id="held-up" className="m-0 text-xl font-extrabold">
            Held up at ioTec
          </h2>
          <p className="m-0 text-md text-muted">
            Mobile money payouts ioTec has taken nothing of for two hours or more. Finvest keeps sending them. Ask ioTec why.
          </p>
        </div>
        {heldUp.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading payouts held up at ioTec
          </p>
        ) : heldUp.state === "failed" ? (
          <Failure title="Payouts held up at ioTec could not be loaded" message={heldUp.message} onRetry={() => void reloadHeldUp()} />
        ) : heldUp.value.length === 0 ? (
          <Empty>Nothing is held up. A payout appears here when ioTec has taken nothing of it for two hours.</Empty>
        ) : (
          <HeldUpTable items={heldUp.value} />
        )}
      </section>

      {canSettle ? (
        <section aria-labelledby="stranded" className="flex flex-col gap-3">
          <div>
            <h2 id="stranded" className="m-0 text-xl font-extrabold">
              Never answered for
            </h2>
            <p className="m-0 text-md text-muted">
              Payouts ioTec was asked about for three days with no answer. Two people settle each one from the ioTec statement.
            </p>
          </div>
          {done ? (
            <p role="status" className="m-0 rounded-md bg-success-surface px-3.5 py-3 text-md font-semibold text-success">
              {done}
            </p>
          ) : null}
          {stranded.state === "loading" ? (
            <p role="status" className="m-0 text-md text-muted">
              Loading payouts never answered for
            </p>
          ) : stranded.state === "failed" ? (
            <Failure title="Payouts never answered for could not be loaded" message={stranded.message} onRetry={() => void reloadStranded()} />
          ) : stranded.value.length === 0 ? (
            <Empty>Every payout ioTec was asked about has an answer.</Empty>
          ) : (
            <div className="flex flex-wrap items-start gap-5">
              <ul aria-label="Payouts never answered for" className="m-0 flex min-w-0 flex-[1_1_240px] list-none flex-col gap-2.5 p-0">
                {stranded.value.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      aria-current={item.id === current?.id ? "true" : undefined}
                      onClick={() => {
                        setSelected(item.id);
                        setFailed(null);
                        setDone(null);
                        pending.current = null;
                      }}
                      className={cn(
                        "flex w-full cursor-pointer flex-col rounded-lg border bg-surface px-4 py-3.5 text-left text-ink hover:bg-surface-sunken",
                        item.id === current?.id ? "border-2 border-black px-[15px] py-[13px]" : "border-border",
                      )}
                    >
                      <span className="text-md font-bold tabular-nums">{formatExact(item.amount)}</span>
                      <span className="text-xs text-muted">First sent {formatMoment(item.firstSentAt)}</span>
                      <span className={cn("mt-1 text-xs font-bold", item.resolution ? "text-muted" : "text-warning-text")}>
                        {item.resolution
                          ? item.resolution.proposedBy === me
                            ? "Waiting for a second person"
                            : "Needs a second person"
                          : "Needs a proposal"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="min-w-0 flex-[3_1_480px]">
                {current ? (
                  <Settle
                    key={`${current.id}:${current.resolution?.id ?? "open"}`}
                    payout={current}
                    me={me}
                    busy={busy}
                    error={failed?.id === current.id ? failed.message : null}
                    onCommand={(command) => void run(current, command)}
                  />
                ) : null}
              </div>
            </div>
          )}
        </section>
      ) : null}
    </>
  );
}

const DONE: Record<SettleCommand["action"], string> = {
  propose: "Proposed. A different person must now approve it.",
  approve: "Settled.",
  decline: "Declined. The payout is waiting for a new proposal.",
  withdraw: "Withdrawn. The payout is waiting for a new proposal.",
};

// Refusals that mean the list on screen is out of date.
const REFRESH: ReadonlySet<string> = new Set([
  "not_found",
  "refused",
  "payout_not_stranded",
  "resolution_already_proposed",
  "resolution_already_decided",
]);

function HeldUpTable({ items }: { items: readonly HeldUpPayout[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Payouts held up at ioTec">
      <table className="w-full min-w-[640px] border-collapse text-md">
        <caption className="sr-only">Payouts held up at ioTec, longest first</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Amount", "Asked for", "First tried", "Last tried", "Sends tried", "Payout"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} className="border-t border-divider">
              <th scope="row" className="px-3 py-3 text-left font-bold tabular-nums">
                {formatExact(item.amount)}
              </th>
              <td className="px-3 py-3">{formatMoment(item.createdAt)}</td>
              <td className="px-3 py-3">{formatMoment(item.firstTriedAt)}</td>
              <td className="px-3 py-3">{formatMoment(item.lastTriedAt)}</td>
              <td className="px-3 py-3 tabular-nums">{item.tries}</td>
              <td className="px-3 py-3 font-mono text-xs">{item.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Empty({ children }: { children: string }) {
  return <p className="m-0 max-w-[640px] rounded-xl border border-border bg-surface p-5 text-md leading-normal text-muted">{children}</p>;
}

function Failure({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h3 className="m-0 text-lg font-extrabold">{title}</h3>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </section>
  );
}

function stubCase(): StubCase {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" ? sample : "sample";
}

/** Sample payouts under `pnpm dev` or a preview build, like sign-in. Otherwise the real API. */
function defaultApi(me: string): PayoutsApi {
  if (isStubAuth()) return createStubPayoutsApi(me, 150, stubCase());
  const base = apiBaseUrl();
  return base ? createHttpPayoutsApi(createHttp(base)) : notConnectedPayoutsApi;
}

/** Bank payouts and returns, the same way: samples under the stub, the real routes otherwise. */
function defaultBankApis(me: string): { bank: BankPayoutsApi; returns: ReturnsApi; directory: Directory } {
  if (isStubAuth())
    return { bank: createStubBankPayoutsApi(me, 150, stubCase()), returns: createStubReturnsApi(me, 150, stubCase()), directory: createStubDirectory() };
  const base = apiBaseUrl();
  if (!base) return { bank: notConnectedBankPayoutsApi, returns: notConnectedReturnsApi, directory: emptyDirectory };
  const http = createHttp(base);
  return { bank: createHttpBankPayoutsApi(http), returns: createHttpReturnsApi(http), directory: createHttpDirectory(http) };
}

export default function MoneyMovementPage() {
  const { session } = useAuth();
  const me = session?.staffId ?? "";
  const [api] = useState<PayoutsApi>(() => defaultApi(me));
  const [bank] = useState(() => defaultBankApis(me));
  const canPayBank = session ? can(session, "bank_payouts.change") : false;
  return (
    <>
      <MoneyMovementScreen api={api} me={me} canSettle={session ? can(session, "reconciliation.change") : false} />
      <BankPayouts api={bank.bank} me={me} canChange={canPayBank} directory={bank.directory} />
      <Returns api={bank.returns} me={me} canChange={canPayBank} directory={bank.directory} />
    </>
  );
}
