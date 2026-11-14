import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  BankTransferError,
  bankTransferErrorMessage,
  notConnectedBankTransfersApi,
  type BankTransfersApi,
  type QueueItem,
  type TransferDetail,
} from "./api.ts";
import { Queue } from "./Queue.tsx";
import { createHttpBankTransfersApi } from "./http.ts";
import { createStubBankTransfersApi, type StubCase } from "./stub.ts";
import { Transfer, type TransferCommand } from "./Transfer.tsx";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

interface BankTransfersScreenProps {
  api: BankTransfersApi;
  /** Whether the signed-in person may match, refuse and approve. Seeing transfers needs less. */
  canDecide: boolean;
  /** Makes the idempotency key for one command. Every try at the same command carries the same key. */
  newKey?: () => string;
}

export function BankTransfersScreen({ api, canDecide, newKey = () => crypto.randomUUID() }: BankTransfersScreenProps) {
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("transfer");
  const [queue, setQueue] = useState<Load<QueueItem[]>>({ state: "loading" });
  // Kept with the transfer it belongs to, so choosing another one shows neither.
  const [loaded, setLoaded] = useState<{ id: string; load: Load<TransferDetail> } | null>(null);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // The transfer being worked on stays open after a decision, even though it leaves the queue.
  const [kept, setKept] = useState<string | null>(null);
  // The key of a command that has not been answered yet. A second press of the same command sends
  // the same key, so a first try that did reach the service is replayed, not applied twice.
  const pending = useRef<{ request: string; key: string } | null>(null);

  const fetchQueue = useCallback(
    (): Promise<Load<QueueItem[]>> =>
      api.listQueue().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: bankTransferErrorMessage(error) }),
      ),
    [api],
  );
  const loadQueue = async () => setQueue(await fetchQueue());

  useEffect(() => {
    let current = true;
    void fetchQueue().then((load) => current && setQueue(load));
    return () => {
      current = false;
    };
  }, [fetchQueue]);

  const items = queue.state === "ready" ? queue.value : null;
  const selectedId = requestedId ?? kept ?? items?.[0]?.id ?? null;

  const loadTransfer = useCallback(
    (id: string) =>
      api.getTransfer(id).then(
        (value): Load<TransferDetail> => ({ state: "ready", value }),
        (error: unknown): Load<TransferDetail> => ({ state: "failed", message: bankTransferErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    if (!selectedId) return;
    let current = true;
    void loadTransfer(selectedId).then((load) => current && setLoaded({ id: selectedId, load }));
    return () => {
      current = false;
    };
  }, [loadTransfer, selectedId]);

  const transfer: Load<TransferDetail> = loaded?.id === selectedId ? loaded.load : { state: "loading" };

  function keyFor(request: string): string {
    if (pending.current?.request === request) return pending.current.key;
    const key = newKey();
    pending.current = { request, key };
    return key;
  }

  async function run(command: TransferCommand) {
    if (!selectedId) return;
    const id = selectedId;
    setBusy(true);
    setFailed(null);
    setKept(id);
    const key = keyFor(JSON.stringify({ id, command }));
    try {
      const updated =
        command.action === "match"
          ? await api.match(key, id, command.statementLineId)
          : command.action === "refuse"
            ? await api.refuse(key, id, command.reason)
            : await api.approve(key, id);
      pending.current = null;
      setLoaded({ id, load: { state: "ready", value: updated } });
      await loadQueue();
    } catch (error) {
      // Only a definite refusal closes the command. An answer that never arrived, or an error
      // we do not recognise, is an unknown outcome, so the next try reuses the same key.
      if (error instanceof BankTransferError && error.code !== "unavailable") pending.current = null;
      setFailed({ id, message: bankTransferErrorMessage(error) });
      if (error instanceof BankTransferError && ["conflict", "not_found", "line_not_found", "line_already_used"].includes(error.code)) {
        setLoaded({ id, load: await loadTransfer(id) });
        await loadQueue();
      }
    } finally {
      setBusy(false);
    }
  }

  const toMatch = items?.filter((item) => item.stage === "to_match").length ?? 0;
  const toApprove = items?.filter((item) => item.stage === "to_approve").length ?? 0;
  // Until the list says which check each transfer waits for, the queue can only count them.
  const unsorted = items?.filter((item) => item.stage === "in_review").length ?? 0;
  const summary =
    items === null
      ? "Bank transfers clients told us about, checked against the bank statement by two people."
      : toMatch + toApprove + unsorted === 0
        ? "Nothing is waiting. New transfers appear here as clients send their receipts."
        : unsorted > 0
          ? `${count(toMatch + toApprove + unsorted, "waiting to be checked")}. Open one to see whether it needs matching or a second person.`
          : `${count(toMatch, "to match")}, ${count(toApprove, "waiting for a second person")}.`;

  return (
    <>
      <PageHeader title="Reconciliation" summary="The ledger compared with provider and bank statements, and anything that does not match." />
      <div>
        <h2 className="m-0 text-xl font-extrabold">Bank transfers to check</h2>
        <p className="m-0 text-md text-muted">{summary}</p>
      </div>
      {queue.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading bank transfers
        </p>
      ) : queue.state === "failed" ? (
        <Failure message={queue.message} onRetry={() => void loadQueue()} />
      ) : queue.value.length === 0 && !requestedId && !kept ? (
        <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Nothing to check</h2>
          <p className="m-0 text-md leading-normal text-muted">
            Every bank transfer has been confirmed or refused. New ones appear here when a client sends a receipt.
          </p>
        </section>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <div className="min-w-0 flex-[1_1_240px]">
            <Queue
              items={queue.value}
              selectedId={selectedId}
              onSelect={(id) => {
                setKept(null);
                pending.current = null;
                setParams({ transfer: id });
              }}
            />
          </div>
          <div className="min-w-0 flex-[3_1_520px]">
            {transfer.state === "loading" ? (
              <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
                Loading this transfer
              </p>
            ) : transfer.state === "failed" ? (
              <Failure message={transfer.message} />
            ) : (
              <Transfer
                key={`${transfer.value.id}:${transfer.value.stage}`}
                transfer={transfer.value}
                canDecide={canDecide}
                busy={busy}
                error={failed?.id === selectedId ? failed.message : null}
                onCommand={(command) => void run(command)}
              />
            )}
          </div>
        </div>
      )}
    </>
  );
}

function count(value: number, what: string): string {
  return `${value === 0 ? "None" : value} ${what}`;
}

function Failure({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 className="m-0 text-xl font-extrabold">Bank transfers could not be loaded</h2>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </section>
  );
}

function stubCase(): StubCase {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" || sample === "unsorted" ? sample : "sample";
}

export default function ReconciliationPage() {
  const { session } = useAuth();
  // Sample transfers under `pnpm dev` or a preview build, like sign-in. `?sample=empty` and
  // `?sample=error` show those states. Otherwise the real routes.
  const [api] = useState<BankTransfersApi>(() => {
    if (!session) return notConnectedBankTransfersApi;
    if (isStubAuth()) return createStubBankTransfersApi(session.staffId, 150, stubCase());
    const base = apiBaseUrl();
    return base ? createHttpBankTransfersApi(createHttp(base), session.staffId) : notConnectedBankTransfersApi;
  });
  return <BankTransfersScreen api={api} canDecide={session ? can(session, "bank_transfers.change") : false} />;
}
