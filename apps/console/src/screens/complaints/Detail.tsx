import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { StaffCard } from "@/directory/api.ts";
import { formatExact } from "@/screens/dealing-desk/format.ts";
import { MAX_NOTE, MAX_RESOLUTION, type Closing, type ComplaintDetail, type MoveTo, type Outcome } from "./api.ts";
import {
  ACTION_LABELS,
  CATEGORY_LABELS,
  kampalaDate,
  MOVES,
  OUTCOME_HINTS,
  OUTCOME_LABELS,
  OUTCOMES,
  received,
  shortDate,
  stamp,
  STATUS_LABELS,
} from "./labels.ts";
import { StatusChip } from "./Register.tsx";

export type Command =
  | { action: "assign"; ownerId: string }
  | { action: "move"; status: MoveTo; note: string }
  | { action: "note"; note: string }
  | { action: "close"; closing: Closing };

interface DetailPanelProps {
  detail: ComplaintDetail;
  clientName: string;
  staffName: (staffId: string | null) => string;
  team: readonly StaffCard[];
  canManage: boolean;
  canClose: boolean;
  busy: boolean;
  error: string | null;
  /** Resolves true once the command is done, so its form can close. */
  onCommand: (command: Command) => Promise<boolean>;
}

type Form = "move" | "note" | "close" | null;

const FIELD = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink";

export function DetailPanel({ detail, clientName, staffName, team, canManage, canClose, busy, error, onCommand }: DetailPanelProps) {
  const { complaint, history } = detail;
  const open = complaint.status !== "closed";
  const [form, setForm] = useState<Form>(null);
  const [moveTo, setMoveTo] = useState<MoveTo>("investigating");
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [resolution, setResolution] = useState("");
  const [accepted, setAccepted] = useState<boolean | null>(null);
  const [tried, setTried] = useState(false);

  function show(next: Form, status?: MoveTo) {
    setForm(next);
    setTried(false);
    if (status) setMoveTo(status);
  }

  async function submit(command: Command | null) {
    setTried(true);
    if (!command) return;
    if (await onCommand(command)) {
      setForm(null);
      setNote("");
      setTried(false);
    }
  }

  const noteMissing = note.trim().length === 0;
  const closeMissing = outcome === null || resolution.trim().length === 0 || accepted === null;

  return (
    <section
      aria-label={`${complaint.reference}, ${clientName}`}
      className="flex flex-col gap-3 rounded-[20px] border border-border bg-surface p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-[18px] font-extrabold">
          {complaint.reference}, {clientName}
        </h2>
        <StatusChip complaint={complaint} />
      </div>
      <p className="m-0 text-md font-bold">{complaint.summary}</p>
      <p className="m-0 whitespace-pre-wrap text-sm leading-normal text-muted">{complaint.details}</p>
      <dl className="m-0 flex flex-col text-sm">
        <Row name="Received">
          {shortDate(kampalaDate(complaint.receivedAt))}, {received(complaint.receivedVia)}
        </Row>
        <Row name="Category">{CATEGORY_LABELS[complaint.category]}</Row>
        {complaint.amount ? <Row name="Amount involved">{formatExact(complaint.amount)}</Row> : null}
        {complaint.conversationId ? <Row name="Linked to">A help chat</Row> : null}
        <Row name="Promised reply by">
          <span className={complaint.overdue ? "text-danger" : undefined}>
            {shortDate(complaint.dueOn)}
            {complaint.overdue ? ", past" : ""}
          </span>
        </Row>
        <Row name="Owner">
          {canManage && open && team.length > 0 ? (
            <label>
              <span className="sr-only">Owner</span>
              <select
                value={complaint.ownerId ?? ""}
                disabled={busy}
                onChange={(event) => void onCommand({ action: "assign", ownerId: event.target.value })}
                className="h-9 rounded-md border border-border-strong bg-surface px-2 text-sm font-semibold"
              >
                {complaint.ownerId ? null : <option value="">No one yet</option>}
                {team.map((person) => (
                  <option key={person.staffId} value={person.staffId}>
                    {person.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            staffName(complaint.ownerId)
          )}
        </Row>
      </dl>

      {complaint.status === "closed" && complaint.outcome ? (
        <div className="flex flex-col gap-1 rounded-md bg-success-surface px-3 py-2.5 text-sm">
          <strong>
            {OUTCOME_LABELS[complaint.outcome]}
            {complaint.closedAt ? `, closed ${shortDate(kampalaDate(complaint.closedAt))}` : ""}
          </strong>
          <span className="whitespace-pre-wrap">{complaint.resolution}</span>
          <span className="text-muted">
            {complaint.clientAccepted ? "The client accepted this." : "The client did not accept this."}
          </span>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}

      {open && form === null && (canManage || canClose) ? (
        <div className="flex flex-col gap-2">
          {canClose ? (
            <Button size="block" disabled={busy} onClick={() => show("close")}>
              Record the outcome
            </Button>
          ) : null}
          {canManage ? (
            <>
              {complaint.status === "escalated" ? null : (
                <Button size="block" variant="secondary" disabled={busy} onClick={() => show("move", "escalated")}>
                  Escalate to a manager
                </Button>
              )}
              <div className="flex gap-2">
                <Button size="block" variant="secondary" className="flex-1" disabled={busy} onClick={() => show("move", "investigating")}>
                  Change status
                </Button>
                <Button size="block" variant="secondary" className="flex-1" disabled={busy} onClick={() => show("note")}>
                  Add a note
                </Button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {form === "move" ? (
        <form
          aria-label="Change status"
          className="flex flex-col gap-3 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit(noteMissing ? null : { action: "move", status: moveTo, note });
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            Move it to
            <select value={moveTo} onChange={(event) => setMoveTo(event.target.value as MoveTo)} className={FIELD}>
              {MOVES.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABELS[status]}
                </option>
              ))}
            </select>
          </label>
          <NoteField label="Why?" value={note} onChange={setNote} missing={tried && noteMissing} />
          <FormButtons busy={busy} label={moveTo === "escalated" ? "Escalate" : "Move it"} onCancel={() => show(null)} />
        </form>
      ) : null}

      {form === "note" ? (
        <form
          aria-label="Add a note"
          className="flex flex-col gap-3 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit(noteMissing ? null : { action: "note", note });
          }}
        >
          <NoteField label="What did you find or do?" value={note} onChange={setNote} missing={tried && noteMissing} />
          <FormButtons busy={busy} label="Add note" onCancel={() => show(null)} />
        </form>
      ) : null}

      {form === "close" ? (
        <form
          aria-label="Record the outcome"
          className="flex flex-col gap-3 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit(
              outcome === null || accepted === null || resolution.trim().length === 0
                ? null
                : { action: "close", closing: { outcome, resolution, clientAccepted: accepted } },
            );
          }}
        >
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1.5 text-sm font-bold">Outcome</legend>
            {OUTCOMES.map((option) => (
              <label key={option} className="flex items-start gap-2 text-sm">
                <input type="radio" name="outcome" checked={outcome === option} onChange={() => setOutcome(option)} className="mt-0.5" />
                <span>
                  <strong>{OUTCOME_LABELS[option]}</strong>
                  <span className="text-muted">. {OUTCOME_HINTS[option]}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            What was done about it
            <span className="text-xs font-medium text-muted">The client reads this in the app.</span>
            <textarea
              value={resolution}
              rows={3}
              maxLength={MAX_RESOLUTION}
              onChange={(event) => setResolution(event.target.value)}
              className={FIELD}
            />
          </label>
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1.5 text-sm font-bold">Did the client accept it?</legend>
            <div className="flex gap-4">
              {[true, false].map((value) => (
                <label key={String(value)} className="flex items-center gap-2 text-sm">
                  <input type="radio" name="accepted" checked={accepted === value} onChange={() => setAccepted(value)} />
                  {value ? "Yes" : "No"}
                </label>
              ))}
            </div>
          </fieldset>
          {tried && closeMissing ? (
            <p className="m-0 text-xs font-bold text-danger">Choose an outcome, say what was done, and whether the client accepted it.</p>
          ) : null}
          <FormButtons busy={busy} label="Close the complaint" onCancel={() => show(null)} />
        </form>
      ) : null}

      <div className="flex flex-col gap-2 border-t border-divider pt-3">
        <h3 className="m-0 text-sm font-extrabold">History</h3>
        <ol aria-label="History" className="m-0 flex list-none flex-col gap-2.5 p-0">
          {history.map((item) => (
            <li key={item.id} className="flex flex-col gap-0.5 text-sm">
              <span className="font-bold">
                {ACTION_LABELS[item.action]}
                {item.action === "status" ? ` to ${STATUS_LABELS[item.toStatus ?? "new"].toLowerCase()}` : ""}
                <span className="font-medium text-muted">
                  , {item.actor === "client" ? "the client" : staffName(item.actorId)}, {stamp(item.occurredAt)}
                </span>
              </span>
              {item.note ? <span className="whitespace-pre-wrap text-muted">{item.note}</span> : null}
            </li>
          ))}
        </ol>
      </div>
      {open ? (
        <p className="m-0 text-xs leading-normal text-muted">
          Closing needs an outcome, what was done, and whether the client accepted it. Nothing in a complaint is ever deleted.
        </p>
      ) : null}
    </section>
  );
}

function Row({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-divider py-2 first:border-t-0">
      <dt className="text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold">{children}</dd>
    </div>
  );
}

function NoteField({ label, value, onChange, missing }: { label: string; value: string; onChange: (text: string) => void; missing: boolean }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-bold">
      {label}
      <textarea
        value={value}
        rows={3}
        maxLength={MAX_NOTE}
        aria-invalid={missing}
        onChange={(event) => onChange(event.target.value)}
        className={`${FIELD} aria-invalid:border-danger`}
      />
      {missing ? <span className="text-xs font-bold text-danger">Write a short note for the record.</span> : null}
      <span className="text-xs font-medium text-muted">Staff only. The client never sees notes.</span>
    </label>
  );
}

function FormButtons({ busy, label, onCancel }: { busy: boolean; label: string; onCancel: () => void }) {
  return (
    <div className="flex gap-2">
      <Button type="submit" disabled={busy}>
        {busy ? "Saving" : label}
      </Button>
      <Button variant="secondary" disabled={busy} onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
