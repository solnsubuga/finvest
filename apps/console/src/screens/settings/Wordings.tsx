import { useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import type { Decision, Field, Problem, Wording, WordingText } from "./api.ts";
import { eventLabel, stamp } from "./labels.ts";
import { checkWording, fillSample, LIMITS, problemText } from "./rules.ts";

const FIELD_LABELS: Record<Field, string> = { title: "Title", body: "Message", sms: "Text message (SMS)" };
const FIELD_HINTS: Record<Field, string> = {
  title: "In the app, push notifications and the email subject.",
  body: "In the app, push notifications and emails.",
  sms: "Sent when the client chose SMS. Start with Finvest so they know who it is from.",
};
const FIELDS: readonly Field[] = ["title", "body", "sms"];
const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";

/** One message as a client would see it, with sample values. */
function Preview({ text, label }: { text: WordingText; label: string }) {
  return (
    <figure aria-label={label} className="m-0 flex flex-col gap-2 rounded-[16px] bg-paper p-4">
      <p className="m-0 text-md font-bold">{text.title}</p>
      <p className="m-0 text-sm leading-normal">{text.body}</p>
      <p className="m-0 border-t border-divider pt-2 text-xs leading-normal text-muted">
        <span className="font-bold">SMS: </span>
        {text.sms}
      </p>
    </figure>
  );
}

interface WordingPanelProps {
  wording: Wording;
  canChange: boolean;
  staffName: (staffId: string) => string;
  busy: boolean;
  error: string | null;
  /** Problems the backend found in the last words sent. */
  refused: readonly Problem[];
  onPropose: (text: WordingText, reason: string) => Promise<boolean>;
  onDecide: (decision: Decision, reason: string) => Promise<boolean>;
}

export function WordingPanel({ wording, canChange, staffName, busy, error, refused, onPropose, onDecide }: WordingPanelProps) {
  const { proposal } = wording;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<WordingText>(wording.wording);
  const [reason, setReason] = useState("");
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);

  const problems = editing ? [...checkWording(draft, wording.variables), ...(tried ? refused : [])] : [];
  const unchanged = FIELDS.every((field) => draft[field] === wording.wording[field]);
  const problemsFor = (field: Field) => [...new Set(problems.filter((p) => p.field === field).map((p) => p.code))];

  return (
    <section
      aria-label={eventLabel(wording.event)}
      className="flex min-w-0 flex-col gap-3 rounded-[20px] border border-border bg-surface p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-[18px] font-extrabold">{eventLabel(wording.event)}</h3>
        {wording.edited && wording.changedAt ? (
          <span className="text-xs font-bold text-muted">Changed {stamp(wording.changedAt)}</span>
        ) : (
          <span className="text-xs font-bold text-muted">The words Finvest shipped with</span>
        )}
      </div>
      <p className="m-0 text-sm text-muted">
        {wording.variables.length === 0
          ? "This message has no values to fill in."
          : `Values it can use: ${wording.variables.map((name) => `{${name}}`).join(", ")}. The preview fills them with samples.`}
      </p>
      <Preview text={wording.preview} label="What clients see now" />

      {proposal ? (
        <div className="flex flex-col gap-3 rounded-[16px] border-2 border-warning p-4">
          <div className="flex flex-col gap-0.5">
            <h4 className="m-0 text-md font-extrabold">A change is waiting for a second person</h4>
            <p className="m-0 text-xs text-muted">
              Proposed by {proposal.yours ? "you" : staffName(proposal.proposedBy)}, {stamp(proposal.proposedAt)}
            </p>
          </div>
          <Preview text={proposal.preview} label="The proposed words" />
          <p className="m-0 text-sm">
            <span className="font-bold">Why: </span>
            {proposal.reason}
          </p>
          {canChange && deciding === null ? (
            <div className="flex flex-wrap gap-2">
              {proposal.yours ? (
                <>
                  <Button variant="secondary" disabled={busy} onClick={() => setDeciding("withdrawn")}>
                    Withdraw it
                  </Button>
                  <p className="m-0 self-center text-xs text-muted">Someone else must approve or reject your change.</p>
                </>
              ) : (
                <>
                  <Button disabled={busy} onClick={() => setDeciding("approved")}>
                    Approve
                  </Button>
                  <Button variant="secondary" disabled={busy} onClick={() => setDeciding("rejected")}>
                    Reject
                  </Button>
                </>
              )}
            </div>
          ) : null}
          {deciding ? (
            <form
              aria-label="Decide the change"
              className="flex flex-col gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                setTried(true);
                if (note.trim().length === 0) return;
                void onDecide(deciding, note).then((done) => {
                  if (done) {
                    setDeciding(null);
                    setNote("");
                    setTried(false);
                  }
                });
              }}
            >
              <label className="flex flex-col gap-1.5 text-sm font-bold">
                {deciding === "approved" ? "Why approve it?" : deciding === "rejected" ? "Why reject it?" : "Why withdraw it?"}
                <textarea
                  value={note}
                  rows={2}
                  aria-invalid={tried && note.trim().length === 0}
                  onChange={(event) => setNote(event.target.value)}
                  className={INPUT}
                />
              </label>
              {tried && note.trim().length === 0 ? (
                <p className="m-0 text-xs font-bold text-danger">Write a short reason for the audit trail.</p>
              ) : null}
              <div className="flex gap-2">
                <Button type="submit" disabled={busy}>
                  {busy ? "Saving" : deciding === "approved" ? "Approve the change" : deciding === "rejected" ? "Reject the change" : "Withdraw the change"}
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => setDeciding(null)}>
                  Cancel
                </Button>
              </div>
            </form>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}

      {!canChange ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2.5 text-sm text-muted">
          Your role can read these messages. Changing them needs an admin.
        </p>
      ) : proposal ? null : editing ? (
        <form
          aria-label="Change the words"
          className="flex flex-col gap-3 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (checkWording(draft, wording.variables).length > 0 || reason.trim().length === 0 || unchanged) return;
            void onPropose(draft, reason).then((done) => {
              if (done) {
                setEditing(false);
                setReason("");
                setTried(false);
              }
            });
          }}
        >
          {FIELDS.map((field) => {
            const codes = problemsFor(field);
            return (
              <label key={field} className="flex flex-col gap-1.5 text-sm font-bold">
                <span className="flex justify-between gap-2">
                  {FIELD_LABELS[field]}
                  <span className={cn("text-xs font-semibold", draft[field].length > LIMITS[field] ? "text-danger" : "text-muted")}>
                    {draft[field].length} of {LIMITS[field]}
                  </span>
                </span>
                <span className="text-xs font-medium text-muted">{FIELD_HINTS[field]}</span>
                <textarea
                  value={draft[field]}
                  rows={field === "title" ? 1 : 3}
                  aria-invalid={codes.length > 0}
                  onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
                  className={INPUT}
                />
                {codes.map((code) => (
                  <span key={code} className="text-xs font-bold text-danger">
                    {problemText(code, field, wording.variables)}
                  </span>
                ))}
              </label>
            );
          })}
          <Preview
            text={{ title: fillSample(draft.title), body: fillSample(draft.body), sms: fillSample(draft.sms) }}
            label="How the new words read"
          />
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            Why change them?
            <span className="text-xs font-medium text-muted">The second person reads this, and it goes in the audit trail.</span>
            <textarea
              value={reason}
              rows={2}
              aria-invalid={tried && reason.trim().length === 0}
              onChange={(event) => setReason(event.target.value)}
              className={INPUT}
            />
          </label>
          {tried && reason.trim().length === 0 ? <p className="m-0 text-xs font-bold text-danger">Say why, for the second person.</p> : null}
          {tried && unchanged ? <p className="m-0 text-xs font-bold text-danger">These are the words in use now. Change something first.</p> : null}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Sending" : "Send for approval"}
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setDraft(wording.wording);
                setTried(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Change the words
          </Button>
          <p className="m-0 text-xs text-muted">A second person approves a change before any client sees it.</p>
        </div>
      )}
    </section>
  );
}

export function WordingRow({ wording, selected, onSelect }: { wording: Wording; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "flex w-full cursor-pointer items-center justify-between gap-2 rounded-[14px] bg-surface px-3.5 py-3 text-left text-sm text-ink",
        selected ? "border-2 border-black px-[13px] py-[11px]" : "border border-border",
      )}
    >
      <span className="min-w-0">
        <span className="block font-bold">{eventLabel(wording.event)}</span>
        <span className="block truncate text-xs text-muted">{wording.preview.title}</span>
      </span>
      {wording.proposal ? (
        <span className="shrink-0 rounded-pill bg-warning-surface px-2 py-0.5 text-2xs font-bold text-warning-text">Waiting</span>
      ) : wording.edited ? (
        <span className="shrink-0 rounded-pill bg-surface-sunken px-2 py-0.5 text-2xs font-bold text-black">Edited</span>
      ) : null}
    </button>
  );
}
