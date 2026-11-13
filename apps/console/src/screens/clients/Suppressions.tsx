import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { formatTime } from "./labels.ts";
import { INPUT, ReasonForm } from "./Parts.tsx";
import { suppressionsErrorMessage, SuppressionsError, type Lift, type LiftDecision, type Suppression, type SuppressionsApi } from "./suppressions.ts";

const short = (hash: string) => `${hash.slice(0, 12)}…`;

/** Where an address stands, in words. */
export function describeSuppression(item: Suppression): string {
  if (item.complained) return "Marked a Finvest email as spam. Finvest never emails this address again.";
  if (!item.suppressed) return item.liftedAt ? `Lifted ${formatTime(item.liftedAt)}. Finvest emails this address again.` : "Finvest emails this address.";
  return `Emails stopped ${formatTime(item.lastEventAt ?? item.createdAt)}: a message could not be delivered and the mail server said it never will be.`;
}

function tone(item: Suppression): { label: string; className: string } {
  if (item.complained) return { label: "Spam complaint", className: "bg-danger-surface text-danger" };
  if (!item.suppressed) return { label: "Lifted", className: "bg-success-surface text-success" };
  if (item.openLift) return { label: "Lift waiting", className: "bg-warning-surface text-warning-text" };
  return { label: "Bounced", className: "bg-danger-surface text-danger" };
}

type Form = "propose" | LiftDecision | null;

/** One suppressed address, with what the reader may do about it. */
export function SuppressionCard({
  item,
  title,
  canLift,
  busy,
  error,
  onPropose,
  onDecide,
}: {
  item: Suppression;
  title: string;
  canLift: boolean;
  busy: boolean;
  error: string | null;
  onPropose: (reason: string) => Promise<boolean>;
  onDecide: (lift: Lift, decision: LiftDecision, reason: string) => Promise<boolean>;
}) {
  const [form, setForm] = useState<Form>(null);
  const lift = item.openLift;
  const badge = tone(item);
  const close = (done: boolean) => {
    if (done) setForm(null);
  };
  const mayPropose = canLift && item.suppressed && !item.complained && !lift;
  return (
    <article aria-label={title} className="flex max-w-[720px] flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-[17px] font-extrabold break-all">{title}</h3>
        <span className={cn("rounded-pill px-2 py-0.5 text-2xs font-bold", badge.className)}>{badge.label}</span>
      </div>
      <p className="m-0 text-sm">{describeSuppression(item)}</p>
      {lift ? (
        <div className="flex flex-col gap-2 rounded-lg bg-surface-sunken p-3">
          <p className="m-0 text-sm font-bold">Lift waiting for a second person</p>
          <p className="m-0 text-sm">“{lift.reason}”</p>
          <p className="m-0 text-xs text-muted">
            Proposed by {lift.yours ? "you" : (lift.proposedByName ?? "another member of staff")}, {formatTime(lift.proposedAt)}
          </p>
        </div>
      ) : null}
      {form ? (
        <ReasonForm
          label={form === "propose" ? "Why email this address again?" : form === "approved" ? "Why approve?" : form === "rejected" ? "Why reject?" : "Why withdraw?"}
          hint={form === "propose" ? "Say how you know the address works now. The second person reads this, and it goes in the audit trail." : "This goes in the audit trail with your name."}
          submit={form === "propose" ? "Send for approval" : form === "approved" ? "Approve the lift" : form === "rejected" ? "Reject" : "Withdraw"}
          busy={busy}
          error={error}
          onSubmit={(reason) => void (form === "propose" ? onPropose(reason) : lift ? onDecide(lift, form, reason) : Promise.resolve(false)).then(close)}
          onCancel={() => setForm(null)}
        />
      ) : (
        <>
          {error ? (
            <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
              {error}
            </p>
          ) : null}
          {canLift && lift ? (
            <div className="flex flex-wrap gap-2">
              {lift.yours ? (
                <Button variant="secondary" onClick={() => setForm("withdrawn")}>
                  Withdraw
                </Button>
              ) : (
                <>
                  <Button onClick={() => setForm("approved")}>Approve</Button>
                  <Button variant="secondary" onClick={() => setForm("rejected")}>
                    Reject
                  </Button>
                </>
              )}
            </div>
          ) : null}
          {mayPropose ? (
            <div>
              <Button variant="secondary" onClick={() => setForm("propose")}>
                Propose a lift
              </Button>
            </div>
          ) : null}
          {!canLift && item.suppressed && !item.complained ? (
            <p className="m-0 text-xs text-muted">Lifting needs support, operations or an admin, and a second person.</p>
          ) : null}
        </>
      )}
    </article>
  );
}

/** Runs a lift command and keeps the card's error and the latest state of the address. */
function useLiftCommands(api: SuppressionsApi, refresh: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function run(on: string, command: () => Promise<Lift>): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    setNotice(null);
    try {
      const lift = await command();
      await refresh();
      setNotice(
        lift.status === "proposed"
          ? "Sent. Someone else must approve the lift before Finvest emails this address again."
          : lift.status === "approved"
            ? "Approved. Finvest emails this address again, until the next lasting bounce."
            : lift.status === "rejected"
              ? "Rejected. The address stays suppressed."
              : "Withdrawn.",
      );
      return true;
    } catch (error) {
      setFailed({ on, message: suppressionsErrorMessage(error) });
      if (error instanceof SuppressionsError && ["waiting", "not_found", "not_suppressed", "complained"].includes(error.code)) await refresh();
      return false;
    } finally {
      setBusy(false);
    }
  }

  return {
    busy,
    notice,
    errorFor: (on: string) => (failed?.on === on ? failed.message : null),
    propose: (item: Suppression) => (reason: string) => run(item.addressHash, () => api.propose(item.addressHash, reason)),
    decide: (item: Suppression) => (lift: Lift, decision: LiftDecision, reason: string) => run(item.addressHash, () => api.decide(lift.id, decision, reason)),
  };
}

function Notice({ text }: { text: string | null }) {
  return text ? (
    <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
      {text}
    </p>
  ) : null;
}

/** On a client's page: whether Finvest still emails them. Nothing shows when it always has. */
export function ClientEmailStatus({ api, email, canLift }: { api: SuppressionsApi; email: string; canLift: boolean }) {
  const [item, setItem] = useState<Suppression | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const fetchItem = useCallback(() => api.lookup(email), [api, email]);
  const refresh = useCallback(async () => {
    setItem(await fetchItem());
  }, [fetchItem]);
  const commands = useLiftCommands(api, refresh);

  useEffect(() => {
    let current = true;
    void fetchItem().then(
      (found) => {
        if (current) setItem(found);
      },
      (error: unknown) => {
        if (current) setFailed(suppressionsErrorMessage(error));
      },
    );
    return () => {
      current = false;
    };
  }, [fetchItem]);

  if (failed) return <p className="m-0 text-sm text-muted">Whether Finvest can email this client could not be checked. {failed}</p>;
  if (!item) return null;
  return (
    <div className="flex flex-col gap-2">
      <Notice text={commands.notice} />
      <SuppressionCard
        item={item}
        title={`Email to ${email}`}
        canLift={canLift}
        busy={commands.busy}
        error={commands.errorFor(item.addressHash)}
        onPropose={commands.propose(item)}
        onDecide={commands.decide(item)}
      />
    </div>
  );
}

type ListLoad = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: Suppression[] };
type Lookup = { state: "idle" } | { state: "looking" } | { state: "none"; email: string } | { state: "found"; email: string; hash: string } | { state: "failed"; message: string };

/** The whole list, and a lookup by address, for when no client is open. */
export function SuppressionsSection({ api, canLift }: { api: SuppressionsApi; canLift: boolean }) {
  const [load, setLoad] = useState<ListLoad>({ state: "loading" });
  const [email, setEmail] = useState("");
  const [lookup, setLookup] = useState<Lookup>({ state: "idle" });
  const [open, setOpen] = useState<string | null>(null);
  const inputId = useId();

  const fetchList = useCallback(
    (): Promise<ListLoad> =>
      api.list().then(
        (found) => ({ state: "ready", items: found.suppressions }),
        (error: unknown) => ({ state: "failed", message: suppressionsErrorMessage(error) }),
      ),
    [api],
  );
  const refresh = useCallback(async () => {
    const next = await fetchList();
    if (next.state === "ready") setLoad(next);
  }, [fetchList]);
  const commands = useLiftCommands(api, refresh);

  useEffect(() => {
    let current = true;
    void fetchList().then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetchList]);

  async function find(event: FormEvent) {
    event.preventDefault();
    const text = email.trim();
    if (!text) return;
    setLookup({ state: "looking" });
    try {
      const found = await api.lookup(text);
      if (found) {
        setLookup({ state: "found", email: text, hash: found.addressHash });
        setOpen(found.addressHash);
        // The lookup may find an address beyond the newest 200 in the list.
        setLoad((last) => (last.state === "ready" && !last.items.some((item) => item.addressHash === found.addressHash) ? { state: "ready", items: [found, ...last.items] } : last));
      } else {
        setLookup({ state: "none", email: text });
      }
    } catch (error) {
      setLookup({ state: "failed", message: suppressionsErrorMessage(error) });
    }
  }

  const items = load.state === "ready" ? load.items : [];
  const chosen = items.find((item) => item.addressHash === open);
  const waiting = items.filter((item) => item.openLift).length;

  return (
    <section aria-labelledby="suppressions" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id="suppressions" className="m-0 text-xl font-extrabold">
          Email suppressions
        </h2>
        <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
          Addresses Finvest stopped emailing after a message bounced for good or was marked as spam. Finvest keeps only a fingerprint of each address, so look one up to see
          whose it is. A lift needs a second person, and a spam complaint is never lifted.
        </p>
      </div>
      <form role="search" aria-label="Address lookup" onSubmit={(event) => void find(event)} className="flex max-w-[720px] flex-col gap-2">
        <label htmlFor={inputId} className="text-sm font-bold">
          Look up an email address
        </label>
        <div className="flex gap-2">
          <input id={inputId} type="email" value={email} onChange={(event) => setEmail(event.target.value)} className={INPUT} autoComplete="off" spellCheck={false} />
          <Button type="submit" disabled={lookup.state === "looking"} className="shrink-0 self-center">
            {lookup.state === "looking" ? "Looking" : "Look up"}
          </Button>
        </div>
        {lookup.state === "none" ? (
          <p role="status" className="m-0 text-sm text-muted">
            Finvest has never stopped emailing {lookup.email}.
          </p>
        ) : null}
        {lookup.state === "failed" ? (
          <p role="alert" className="m-0 text-sm font-bold text-danger">
            {lookup.message}
          </p>
        ) : null}
      </form>
      <Notice text={commands.notice} />
      {chosen ? (
        <SuppressionCard
          key={chosen.addressHash}
          item={chosen}
          title={lookup.state === "found" && lookup.hash === chosen.addressHash ? lookup.email : `Address ${short(chosen.addressHash)}`}
          canLift={canLift}
          busy={commands.busy}
          error={commands.errorFor(chosen.addressHash)}
          onPropose={commands.propose(chosen)}
          onDecide={commands.decide(chosen)}
        />
      ) : null}
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading email suppressions
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">Email suppressions could not be loaded</h3>
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
      ) : items.length === 0 ? (
        <p className="m-0 max-w-[640px] rounded-xl border border-border bg-surface p-4 text-md text-muted">Finvest has not stopped emailing anyone.</p>
      ) : (
        <div className="flex max-w-[720px] flex-col gap-2">
          <p className="m-0 text-sm text-muted">
            The newest {items.length} {items.length === 1 ? "address" : "addresses"}
            {waiting > 0 ? `, ${waiting} with a lift waiting for a second person` : ""}.
          </p>
          <ul aria-label="Suppressed addresses" className="m-0 flex list-none flex-col rounded-xl border border-border bg-surface p-0">
            {items.map((item) => {
              const badge = tone(item);
              return (
                <li key={item.addressHash} className="border-b border-border last:border-b-0">
                  <button
                    type="button"
                    aria-pressed={open === item.addressHash}
                    onClick={() => setOpen(open === item.addressHash ? null : item.addressHash)}
                    className="grid w-full cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 bg-transparent px-4 py-3 text-left text-sm text-ink hover:bg-surface-sunken"
                  >
                    <span className="font-mono">{short(item.addressHash)}</span>
                    <span className="text-muted">since {formatTime(item.createdAt)}</span>
                    <span className={cn("rounded-pill px-2 py-0.5 text-2xs font-bold", badge.className)}>{badge.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
