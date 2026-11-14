import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { formatTime } from "./labels.ts";
import { formatPhone } from "./Phone.tsx";
import {
  OUTCOME_LABELS,
  REASON_LABELS,
  RecoveryError,
  recoveryErrorMessage,
  type RecoveryApi,
  type RecoveryOutcome,
  type RecoveryRequest,
} from "./recovery.ts";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: RecoveryRequest[] };

const OUTCOMES: RecoveryOutcome[] = ["number_changed", "helped", "not_the_client"];

/**
 * Clients who cannot get in and asked to be called back, oldest first. Calling them, checking
 * their ID and changing the number happen outside this list: it only leads to the client and
 * records how the call ended.
 */
export function RecoverySection({ api, onOpenClient }: { api: RecoveryApi; onOpenClient: (clientId: string) => void }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [closing, setClosing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchList = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (items) => ({ state: "ready", items }),
        (failure: unknown) => ({ state: "failed", message: recoveryErrorMessage(failure) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchList().then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetchList]);

  async function close(item: RecoveryRequest, outcome: RecoveryOutcome) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.close(item.id, outcome);
      setClosing(null);
      setNotice(`Closed as “${OUTCOME_LABELS[outcome]}”. It is in the client's audit trail.`);
      setLoad(await fetchList());
    } catch (failure) {
      setError(recoveryErrorMessage(failure));
      if (failure instanceof RecoveryError && failure.code === "not_found") setLoad(await fetchList());
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="recovery-heading" className="flex max-w-[720px] flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="recovery-heading" className="m-0 text-xl font-extrabold">
          Locked out, waiting for a call
        </h2>
        <p className="m-0 text-sm text-muted">
          Call each person on the number they gave and check who they are against their ID before you change anything. A new number goes
          through the client's mobile number change, which a second person approves.
        </p>
      </div>
      {notice ? (
        <p role="status" className="m-0 rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
          {error}
        </p>
      ) : null}
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-sm text-muted">
          Loading requests
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-2 rounded-xl border border-border bg-surface p-4">
          <p className="m-0 text-sm font-bold">The requests could not be loaded</p>
          <p className="m-0 text-sm text-muted">{load.message}</p>
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
      ) : load.items.length === 0 ? (
        <p className="m-0 rounded-xl border border-border bg-surface p-4 text-sm text-muted">No one is waiting for a call.</p>
      ) : (
        <ul aria-label="Requests for a call back" className="m-0 flex list-none flex-col gap-3 p-0">
          {load.items.map((item) => (
            <li key={item.id}>
              <article aria-label={`Request for ${formatPhone(item.accountPhone)}`} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="m-0 text-[17px] font-extrabold tabular-nums">{formatPhone(item.accountPhone)}</h3>
                  <span className="rounded-pill bg-warning-surface px-2 py-0.5 text-2xs font-bold text-warning-text">{REASON_LABELS[item.reason]}</span>
                </div>
                <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                  <dt className="text-muted">Call them on</dt>
                  <dd className="m-0 font-bold tabular-nums">
                    {formatPhone(item.contactPhone)}
                    {item.contactPhone === item.accountPhone ? <span className="font-medium text-muted"> (the number on the account)</span> : null}
                  </dd>
                  <dt className="text-muted">Asked</dt>
                  <dd className="m-0">{formatTime(item.createdAt)}</dd>
                </dl>
                {closing === item.id ? (
                  <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
                    <legend className="mb-1 text-sm font-bold">How did the call end?</legend>
                    <div className="flex flex-wrap gap-2">
                      {OUTCOMES.map((outcome) => (
                        <Button key={outcome} variant="secondary" disabled={busy} onClick={() => void close(item, outcome)}>
                          {OUTCOME_LABELS[outcome]}
                        </Button>
                      ))}
                      <Button variant="secondary" disabled={busy} onClick={() => setClosing(null)}>
                        Cancel
                      </Button>
                    </div>
                  </fieldset>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => onOpenClient(item.clientId)}>Open the client</Button>
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setError(null);
                        setClosing(item.id);
                      }}
                    >
                      Close the request
                    </Button>
                  </div>
                )}
              </article>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
