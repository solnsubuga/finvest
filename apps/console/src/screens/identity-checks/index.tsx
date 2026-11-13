import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { KycError, kycErrorMessage, notConnectedKycApi, type CheckDetail, type KycApi, type QueueItem } from "./api.ts";
import { Queue } from "./Queue.tsx";
import { Review, type DecisionInput } from "./Review.tsx";
import { createHttpKycApi } from "./http.ts";
import { createStubKycApi } from "./stub.ts";

/** Sample checks under `pnpm dev` or a preview build, like sign-in. Otherwise the real API. */
function defaultKycApi(): KycApi {
  if (isStubAuth()) return createStubKycApi();
  const base = apiBaseUrl();
  return base ? createHttpKycApi(createHttp(base)) : notConnectedKycApi;
}

const defaultApi: KycApi = defaultKycApi();

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

export function IdentityChecksScreen({
  api = defaultApi,
  now = () => new Date(),
  canDecide = true,
}: {
  api?: KycApi;
  now?: () => Date;
  /** Whether this person holds `identity_checks.review`. */
  canDecide?: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("check");
  const [queue, setQueue] = useState<Load<QueueItem[]>>({ state: "loading" });
  // Each is kept with the check it belongs to, so choosing another check shows neither.
  const [loaded, setLoaded] = useState<{ id: string; load: Load<CheckDetail> } | null>(null);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // The check being worked on stays open after a decision, even though it leaves the queue.
  const [kept, setKept] = useState<string | null>(null);
  // Bumped by "Try again" on a check that failed to load, to fetch that one check again.
  const [attempt, setAttempt] = useState(0);

  const fetchQueue = useCallback(
    (): Promise<Load<QueueItem[]>> =>
      api.listQueue().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: kycErrorMessage(error) }),
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

  // Open the check named in the address, or the first one that needs a person.
  const items = queue.state === "ready" ? queue.value : null;
  const selectedId = requestedId ?? kept ?? items?.[0]?.id ?? null;

  useEffect(() => {
    if (!selectedId) return;
    let current = true;
    const show = (load: Load<CheckDetail>) => current && setLoaded({ id: selectedId, load });
    api.getCheck(selectedId).then(
      (value) => show({ state: "ready", value }),
      (error: unknown) => show({ state: "failed", message: kycErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [api, selectedId, attempt]);

  const check: Load<CheckDetail> = loaded?.id === selectedId ? loaded.load : { state: "loading" };
  const decisionError = failed?.id === selectedId ? failed.message : null;

  async function decide(input: DecisionInput) {
    if (!selectedId) return;
    setBusy(true);
    setFailed(null);
    setKept(selectedId);
    try {
      const updated =
        input.decision === "approve"
          ? await api.approve(selectedId, input.reason)
          : input.decision === "reject"
            ? await api.reject(selectedId, input.reason, input.clientMessage)
            : input.decision === "ask_for_photos"
              ? await api.askForPhotos(selectedId, input.photos, input.clientMessage)
              : await api.escalate(selectedId, input.reason);
      setLoaded({ id: selectedId, load: { state: "ready", value: updated } });
      await loadQueue();
    } catch (error) {
      setFailed({ id: selectedId, message: kycErrorMessage(error) });
      if (error instanceof KycError && error.code === "conflict") {
        await api.getCheck(selectedId).then(
          (value) => setLoaded({ id: selectedId, load: { state: "ready", value } }),
          () => undefined,
        );
        await loadQueue();
      }
    } finally {
      setBusy(false);
    }
  }

  const waiting = items?.filter((item) => item.status === "needs_review").length ?? 0;
  const summary =
    items === null
      ? "ID documents and selfies that need a person to decide."
      : waiting === 0
        ? "Nobody is waiting for a decision."
        : waiting === 1
          ? "One client is waiting for a decision."
          : `${waiting} clients are waiting for a decision.`;

  return (
    <>
      <PageHeader title="Identity checks" summary={summary} />
      {queue.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading identity checks
        </p>
      ) : queue.state === "failed" ? (
        <Failure message={queue.message} onRetry={() => void loadQueue()} />
      ) : queue.value.length === 0 && !requestedId && !kept ? (
        <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Nothing to review</h2>
          <p className="m-0 text-md leading-normal text-muted">
            Every identity check has been decided. New ones appear here as clients submit them.
          </p>
        </section>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <div className="min-w-0 flex-[1_1_240px]">
            <Queue
              items={queue.value}
              selectedId={selectedId}
              now={now()}
              onSelect={(id) => {
                setKept(null);
                setParams({ check: id });
              }}
            />
          </div>
          <div className="min-w-0 flex-[3_1_480px]">
            {check.state === "loading" ? (
              <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
                Loading this check
              </p>
            ) : check.state === "failed" ? (
              <Failure
                title="This check could not be loaded"
                message={check.message}
                onRetry={() => {
                  setLoaded(null);
                  setAttempt((count) => count + 1);
                }}
              />
            ) : (
              <Review
                key={check.value.id}
                check={check.value}
                busy={busy}
                error={decisionError}
                onDecide={(input) => void decide(input)}
                onShowNumber={() => api.showDocumentNumber(check.value.id)}
                canDecide={canDecide}
              />
            )}
          </div>
        </div>
      )}
    </>
  );
}

function Failure({
  title = "Identity checks could not be loaded",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 className="m-0 text-xl font-extrabold">{title}</h2>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </section>
  );
}

export default function IdentityChecksPage() {
  const { session } = useAuth();
  return <IdentityChecksScreen canDecide={session ? can(session, "identity_checks.review") : false} />;
}
