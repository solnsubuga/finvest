import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { CorrectionsError, correctionProblemText, correctionsErrorMessage, failureText, type Correction } from "../investments/corrections/api.ts";
import type { Investment } from "../investments/api.ts";
import { formatExact } from "../products/format.ts";
import type { Amount } from "../products/api.ts";
import { messageErrorText, type HeldActionsApi, type PayoutAccount } from "./actions.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const REASON_LIMIT = 500;
const MESSAGE_LIMIT = 2000;

/** What staff usually write to a client whose money waits. They change it to fit the call. */
export const FIRST_MESSAGE =
  "Your investment has ended and your money is ready. Open Finvest and choose where you would like it sent, and we will send it straight away. Reply here if you need help.";

export type HeldMode = "send" | "message";

interface HeldActionsProps {
  api: HeldActionsApi;
  investment: Investment;
  /** Principal and interest after tax: what a pay out sends. */
  proceeds: Amount;
  /** The latest pay out proposed for this term, if any. */
  latest: Correction | undefined;
  mode: HeldMode;
  onClose: () => void;
  /** After a pay out is proposed, so the screen reads the list again. */
  onProposed: (correction: Correction) => void;
  newKey: () => string;
}

/** The form under a held term: send its money to another of the client's accounts, or write to the client. */
export function HeldActions(props: HeldActionsProps) {
  return props.mode === "send" ? <SendForm {...props} /> : <MessageForm {...props} />;
}

type Accounts = { state: "loading" } | { state: "failed" } | { state: "ready"; items: readonly PayoutAccount[] };

function SendForm({ api, investment, proceeds, onClose, onProposed, newKey }: HeldActionsProps) {
  const [accounts, setAccounts] = useState<Accounts>({ state: "loading" });
  const [chosen, setChosen] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState<readonly string[]>([]);
  // One key per attempt, so a retry after a lost answer is the same request.
  const key = useRef(newKey());

  useEffect(() => {
    let current = true;
    api.payoutAccounts(investment.clientId).then(
      (items) => current && setAccounts({ state: "ready", items }),
      () => current && setAccounts({ state: "failed" }),
    );
    return () => {
      current = false;
    };
  }, [api, investment.clientId]);

  const missingAccount = !chosen;
  const missingReason = !reason.trim();
  const tooLong = reason.length > REASON_LIMIT;

  async function submit() {
    setTried(true);
    if (missingAccount || missingReason || tooLong) return;
    setBusy(true);
    setError(null);
    setRefused([]);
    try {
      const proposed = await api.proposePayOut(key.current, investment.id, chosen, reason.trim());
      onProposed(proposed);
    } catch (caught) {
      // A refusal is final for this key: a changed form is a new attempt.
      if (!(caught instanceof CorrectionsError) || caught.code !== "unavailable") key.current = newKey();
      const problems = caught instanceof CorrectionsError ? caught.problems : [];
      const onAccount = problems.filter((p) => p.field === "destination_id").map((p) => p.code);
      setRefused(onAccount);
      setError(onAccount.length > 0 ? null : correctionsErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      aria-label="Send to another account"
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <h3 className="m-0 text-[17px] font-extrabold">Send to another account</h3>
      <p className="m-0 text-sm leading-normal text-muted">
        For a client who asked, on the phone or in a chat, for the money to go to another account of theirs. All of it goes:{" "}
        <span className="font-bold text-ink tabular-nums">{formatExact(proceeds)}</span>. Someone else approves it before anything is sent.
      </p>
      {accounts.state === "loading" ? (
        <p role="status" className="m-0 text-sm text-muted">
          Loading the client's payout accounts
        </p>
      ) : accounts.state === "failed" ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          The client's payout accounts could not be read. Your role may not include clients, or Finvest could not be reached.
        </p>
      ) : accounts.items.length === 0 ? (
        <p className="m-0 rounded-md bg-warning-surface px-3 py-2.5 text-sm leading-normal font-semibold text-warning-text">
          The client has no approved payout account. Ask them to add one in the app: once it is approved, they can choose it themselves.
        </p>
      ) : (
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0" aria-invalid={tried && missingAccount}>
          <legend className="mb-1.5 p-0 text-sm font-bold">Which account did the client ask for?</legend>
          {accounts.items.map((account) => (
            <label
              key={account.id}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-lg border bg-surface p-3.5",
                chosen === account.id ? "border-2 border-ink p-[13px]" : "border-border-strong",
              )}
            >
              <input
                type="radio"
                name={`account-${investment.id}`}
                checked={chosen === account.id}
                onChange={() => {
                  setChosen(account.id);
                  setRefused([]);
                }}
                className="mt-1 size-4 accent-black"
              />
              <span className="flex flex-col gap-0.5">
                <span className="text-md font-bold">
                  {account.label} <span className="tabular-nums">{account.maskedAccount}</span>
                </span>
                <span className="text-sm text-muted">{account.holderName}</span>
              </span>
            </label>
          ))}
          {tried && missingAccount ? <span className="text-xs font-bold text-danger">Choose the account the client asked for.</span> : null}
          {refused.map((code) => (
            <span key={code} className="text-xs font-bold text-danger">
              {correctionProblemText(code)}
            </span>
          ))}
        </fieldset>
      )}
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        <span className="flex justify-between gap-2">
          Why?
          <span className={cn("text-xs font-semibold", tooLong ? "text-danger" : "text-muted")}>
            {reason.length} of {REASON_LIMIT}
          </span>
        </span>
        <span className="text-xs font-medium text-muted">Staff only, for the person who approves it. Say how and when the client asked.</span>
        <textarea rows={3} value={reason} aria-invalid={(tried && missingReason) || tooLong} onChange={(event) => setReason(event.target.value)} className={INPUT} />
        {tried && missingReason ? <span className="text-xs font-bold text-danger">Fill this in.</span> : null}
        {tooLong ? <span className="text-xs font-bold text-danger">Keep it to 500 characters.</span> : null}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || accounts.state !== "ready" || accounts.items.length === 0}>
          {busy ? "Saving" : "Propose sending it"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function MessageForm({ api, investment, onClose, newKey }: HeldActionsProps) {
  const [body, setBody] = useState(FIRST_MESSAGE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const key = useRef(newKey());
  const empty = !body.trim();
  const tooLong = body.length > MESSAGE_LIMIT;

  async function submit() {
    if (empty || tooLong) return;
    setBusy(true);
    setError(null);
    try {
      await api.message(key.current, investment.clientId, body.trim());
      setSent(true);
    } catch (caught) {
      setError(messageErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <div role="status" className="flex flex-col items-start gap-3">
        <h3 className="m-0 text-[17px] font-extrabold">Message sent</h3>
        <p className="m-0 text-sm leading-normal">It is in the client's help chat, and they are told there is a message. Their reply comes to Support like any other.</p>
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      </div>
    );
  }

  return (
    <form
      aria-label="Message the client"
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <h3 className="m-0 text-[17px] font-extrabold">Message the client</h3>
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        <span className="flex justify-between gap-2">
          Message
          <span className={cn("text-xs font-semibold", tooLong ? "text-danger" : "text-muted")}>{body.length.toLocaleString("en-GB")} of 2,000</span>
        </span>
        <span className="text-xs font-medium text-muted">
          Goes to the client's help chat, opening one if they have none, and they are told. They see your first name. Never ask for a PIN or a code.
        </span>
        <textarea rows={4} value={body} aria-invalid={empty || tooLong} onChange={(event) => setBody(event.target.value)} className={INPUT} />
        {empty ? <span className="text-xs font-bold text-danger">Write the message first.</span> : null}
        {tooLong ? <span className="text-xs font-bold text-danger">Keep it to 2,000 characters.</span> : null}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || empty || tooLong}>
          {busy ? "Sending" : "Send message"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** What became of the latest pay out proposed for a held term, for the row under it. */
export function PayOutNote({ latest }: { latest: Correction | undefined }) {
  if (!latest) return null;
  if (latest.status === "proposed" || latest.status === "approved") {
    return (
      <p className="m-0 mt-2 rounded-md bg-warning-surface px-3 py-2 text-sm leading-normal font-semibold text-warning-text">
        {latest.status === "proposed"
          ? "Sending it to another account waits for a second person. They approve it under Corrections on Investments, or in Approvals."
          : "Approved. Finvest is sending it now."}
      </p>
    );
  }
  if (latest.status === "failed" && latest.failureCode) {
    return (
      <p className="m-0 mt-2 rounded-md bg-danger-surface px-3 py-2 text-sm leading-normal font-semibold text-danger">
        The last try to send it moved nothing. {failureText(latest.failureCode)}
      </p>
    );
  }
  if (latest.status === "sent_back") {
    return <p className="m-0 mt-2 text-sm text-muted">The last try to send it was sent back: {latest.decisionNote}</p>;
  }
  return null;
}
