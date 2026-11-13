import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import type { CheckDetail, Decision, PhotoKind } from "./api.ts";
import {
  CHECK_LABELS,
  countryName,
  flagLabel,
  formatAccountNumber,
  formatDay,
  PHOTO_NAMES,
  photoLabel,
  STATUS_LABELS,
  verdictLabel,
} from "./labels.ts";

export interface DecisionInput {
  decision: Decision;
  reason: string;
  clientMessage: string;
  photos: readonly PhotoKind[];
}

interface ReviewProps {
  check: CheckDetail;
  busy: boolean;
  /** Why the last decision did not go through. */
  error: string | null;
  onDecide: (input: DecisionInput) => void;
  /** Reads the whole document number. Each call is an audit event. */
  onShowNumber: () => Promise<string>;
  /** Whether this person holds `identity_checks.review`. Without it they only look. */
  canDecide?: boolean;
}

const MAX_MESSAGE = 300;
const MAX_REASON = 500;

const DECISION_BUTTONS: Record<Decision, { label: string; variant: "primary" | "accent" | "secondary" }> = {
  approve: { label: "Approve", variant: "primary" },
  ask_for_photos: { label: "Ask for new photos", variant: "accent" },
  reject: { label: "Reject", variant: "secondary" },
  escalate: { label: "Escalate to compliance", variant: "secondary" },
};

/** Approving and rejecting cannot be undone, so each asks once more. */
const CONFIRMATIONS: Partial<Record<Decision, { text: string; confirm: string }>> = {
  approve: {
    text: "Approving opens the account and issues the account number. It cannot be undone.",
    confirm: "Yes, approve",
  },
  reject: { text: "Rejecting is final. This client will not be able to open an account.", confirm: "Yes, reject" },
};

type FieldErrors = Partial<Record<"reason" | "clientMessage" | "photos", string>>;

function problems(input: DecisionInput): FieldErrors {
  const errors: FieldErrors = {};
  if (input.decision === "ask_for_photos") {
    if (input.photos.length === 0) errors.photos = "Choose at least one photo for the client to take again.";
    if (!input.clientMessage.trim()) errors.clientMessage = "Tell the client what to fix.";
  } else if (!input.reason.trim()) {
    errors.reason = "Give the reason for your decision.";
  }
  return errors;
}

const NOT_GIVEN = "Not given yet";

export function Review({ check, busy, error, onDecide, onShowNumber, canDecide = true }: ReviewProps) {
  const fullName = check.surname && check.givenNames ? `${check.surname} ${check.givenNames}` : null;
  const flag = flagLabel(check.reviewFlags);
  // The whole number, once asked for. It is kept only while this check is open.
  const [number, setNumber] = useState<{ state: "hidden" | "loading" | "failed" } | { state: "shown"; value: string }>({ state: "hidden" });

  function toggleNumber() {
    if (number.state === "shown") {
      setNumber({ state: "hidden" });
      return;
    }
    setNumber({ state: "loading" });
    onShowNumber().then(
      (value) => setNumber({ state: "shown", value }),
      () => setNumber({ state: "failed" }),
    );
  }

  return (
    <article
      aria-label={`Identity check for ${check.name ?? fullName ?? "a client"}`}
      className="flex min-w-0 flex-col gap-4 rounded-xl border border-border bg-surface p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-2xl font-extrabold">{check.name ?? fullName ?? "Details not confirmed yet"}</h2>
        <StatusPill check={check} flag={flag} />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {check.photos.map((photo) => {
          const label = photoLabel(check.documentType, photo.kind);
          const asked = check.retakePhotos.includes(photo.kind);
          const selfie = check.automaticChecks.find((item) => item.check === "selfie_match");
          const doubt = photo.kind === "selfie" && selfie && selfie.verdict !== "pass";
          return (
            <figure
              key={photo.kind}
              className={cn(
                "m-0 flex h-[150px] flex-col items-center justify-center gap-1 overflow-hidden rounded-lg text-sm text-muted",
                doubt ? "border-2 border-warning" : "border border-dashed border-neutral",
              )}
            >
              {photo.url ? (
                <a href={photo.url} target="_blank" rel="noreferrer" className="block size-full">
                  <img src={photo.url} alt={label} referrerPolicy="no-referrer" className="size-full object-cover" />
                </a>
              ) : (
                <figcaption>{label}</figcaption>
              )}
              {doubt && selfie.score !== null ? (
                <strong className="text-warning-text">Face match {selfie.score}%</strong>
              ) : null}
              {asked ? <strong className="text-warning-text">New photo asked for</strong> : null}
            </figure>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-5">
        <Section title="Read from the ID">
          <dl className="m-0">
            <Row name="Full name" value={fullName ?? NOT_GIVEN} />
            <Row
              name="ID number"
              value={
                check.documentNumberMasked ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="tabular-nums">{number.state === "shown" ? number.value : check.documentNumberMasked}</span>
                    <button
                      type="button"
                      onClick={toggleNumber}
                      disabled={number.state === "loading"}
                      className="cursor-pointer text-xs font-bold text-muted underline"
                    >
                      {number.state === "shown" ? "Hide" : number.state === "failed" ? "Try again" : "Show"}
                    </button>
                  </span>
                ) : (
                  NOT_GIVEN
                )
              }
            />
            <Row name="Date of birth" value={check.dateOfBirth ? formatDay(check.dateOfBirth) : NOT_GIVEN} />
            <Row name="Expires" value={check.documentExpiresOn ? formatDay(check.documentExpiresOn) : NOT_GIVEN} />
            <Row name="Country of issue" value={countryName(check.documentCountry)} />
          </dl>
        </Section>
        <Section title="Automatic checks">
          {check.automaticChecks.length === 0 ? (
            <p className="m-0 py-2.5 text-md leading-normal text-muted">
              No automatic checks ran on this client. Compare the photos and the details yourself.
            </p>
          ) : (
            <ul className="m-0 list-none p-0">
              {check.automaticChecks.map((item) => (
                <li key={item.check} className="flex items-center gap-2.5 border-b border-divider py-2.5 text-md">
                  <span
                    aria-hidden="true"
                    className={cn("size-2.5 shrink-0 rounded-pill", item.verdict === "pass" ? "bg-black" : "bg-warning")}
                  />
                  <span className="grow">{CHECK_LABELS[item.check]}</span>
                  <span className="font-bold">{verdictLabel(item.check, item.verdict, item.score)}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <Outcome check={check} />
      {check.allowed.length > 0 && canDecide ? (
        <DecisionForm key={`${check.id}:${check.status}`} check={check} busy={busy} error={error} onDecide={onDecide} />
      ) : check.allowed.length > 0 ? (
        <p className="m-0 border-t border-divider pt-4 text-md leading-normal text-muted">
          You can look at this check. Deciding it needs the identity check review permission, held by Admin, Operations
          and Compliance.
        </p>
      ) : null}
    </article>
  );
}

function StatusPill({ check, flag }: { check: CheckDetail; flag: string | null }) {
  const tone =
    check.status === "approved"
      ? "bg-success-surface text-success"
      : check.status === "rejected"
        ? "bg-danger-surface text-danger"
        : check.status === "needs_review"
          ? "bg-warning-surface text-warning-text"
          : "bg-surface-sunken text-muted";
  const text = check.status === "needs_review" && flag ? flag : STATUS_LABELS[check.status];
  return <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", tone)}>{text}</span>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0 flex-[1_1_260px]">
      <h3 className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Row({ name, value }: { name: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-divider py-2.5 text-md">
      <dt className="text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold">{value}</dd>
    </div>
  );
}

/** What has already happened to a check that is not waiting for this person. */
function Outcome({ check }: { check: CheckDetail }) {
  let text: ReactNode = null;
  if (check.status === "approved" && check.accountNumber) {
    text = (
      <>
        Approved. Account number <strong className="tabular-nums">{formatAccountNumber(check.accountNumber)}</strong>{" "}
        was issued.
      </>
    );
  } else if (check.status === "rejected") {
    text = "Rejected. This decision is final.";
  } else if (check.status === "awaiting_client") {
    text = (
      <>
        Waiting for the client to retake: {check.retakePhotos.map((kind) => PHOTO_NAMES[kind].toLowerCase()).join(", ")}.
        {check.clientMessage ? <> They were told: “{check.clientMessage}”</> : null}
      </>
    );
  } else if (check.status === "submitted") {
    text = "Automatic checks are still running. There is nothing to decide yet.";
  } else if (check.status === "with_compliance" && check.allowed.length === 0) {
    text = "With compliance. They will decide this check.";
  }
  if (!text) return null;
  return (
    <p role="status" className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
      {text}
    </p>
  );
}

function DecisionForm({ check, busy, error, onDecide }: Omit<ReviewProps, "onShowNumber" | "canDecide">) {
  const ids = useId();
  const [reason, setReason] = useState("");
  const [clientMessage, setClientMessage] = useState("");
  const [photos, setPhotos] = useState<readonly PhotoKind[]>([]);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [confirming, setConfirming] = useState<Decision | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  // Keyboard and screen reader users land on the question, not on a button that just vanished.
  useEffect(() => {
    if (confirming) confirmButton.current?.focus();
  }, [confirming]);

  function attempt(decision: Decision, confirmed = false) {
    const input = { decision, reason: reason.trim(), clientMessage: clientMessage.trim(), photos };
    const found = problems(input);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setConfirming(null);
      return;
    }
    if (CONFIRMATIONS[decision] && !confirmed) {
      setConfirming(decision);
      return;
    }
    setConfirming(null);
    onDecide(input);
  }

  function toggle(kind: PhotoKind) {
    setPhotos((current) => (current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind]));
  }

  const confirmation = confirming ? CONFIRMATIONS[confirming] : undefined;

  return (
    <form
      aria-label="Decision"
      className="flex flex-col gap-4 border-t border-divider pt-4"
      onSubmit={(event: FormEvent) => event.preventDefault()}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-reason`}>Reason for your decision</Label>
        <Input
          id={`${ids}-reason`}
          value={reason}
          maxLength={MAX_REASON}
          onChange={(event) => setReason(event.target.value)}
          aria-invalid={errors.reason ? true : undefined}
          aria-describedby={`${ids}-reason-help`}
          className="h-11 rounded-md text-md font-medium"
        />
        <FieldNote id={`${ids}-reason-help`} error={errors.reason}>
          Kept in the audit trail. The client does not see it.
        </FieldNote>
      </div>

      {check.allowed.includes("ask_for_photos") || check.allowed.includes("reject") ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-message`}>Message to the client</Label>
          <Input
            id={`${ids}-message`}
            value={clientMessage}
            maxLength={MAX_MESSAGE}
            onChange={(event) => setClientMessage(event.target.value)}
            aria-invalid={errors.clientMessage ? true : undefined}
            aria-describedby={`${ids}-message-help`}
            placeholder="Your selfie was too dark. Please retake it facing a window."
            className="h-11 rounded-md text-md font-medium"
          />
          <FieldNote id={`${ids}-message-help`} error={errors.clientMessage}>
            Sent when you ask for new photos or reject.
          </FieldNote>
        </div>
      ) : null}

      {check.allowed.includes("ask_for_photos") ? (
        <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0" aria-describedby={`${ids}-photos-help`}>
          <legend className="p-0 text-sm font-bold">Photos to take again</legend>
          <div className="flex flex-wrap gap-x-5 gap-y-1">
            {check.photos.map((photo) => (
              <label key={photo.kind} className="flex min-h-11 cursor-pointer items-center gap-2 text-md">
                <input
                  type="checkbox"
                  checked={photos.includes(photo.kind)}
                  onChange={() => toggle(photo.kind)}
                  className="size-4 accent-black"
                />
                {photoLabel(check.documentType, photo.kind)}
              </label>
            ))}
          </div>
          <FieldNote id={`${ids}-photos-help`} error={errors.photos}>
            The client retakes only these. Everything else is kept.
          </FieldNote>
        </fieldset>
      ) : null}

      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
          {error}
        </p>
      ) : null}

      {confirmation && confirming ? (
        <div role="alertdialog" aria-label="Confirm" className="flex flex-col gap-3 rounded-md bg-warning-surface p-3.5">
          <p className="m-0 text-md font-semibold text-warning-text">{confirmation.text}</p>
          <div className="flex flex-wrap gap-2.5">
            <Button ref={confirmButton} disabled={busy} onClick={() => attempt(confirming, true)}>
              {busy ? "Saving" : confirmation.confirm}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setConfirming(null)}>
              Go back
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2.5">
          {check.allowed.map((decision) => (
            <Button
              key={decision}
              variant={DECISION_BUTTONS[decision].variant}
              disabled={busy}
              onClick={() => attempt(decision)}
              className="h-12 flex-[1_1_140px]"
            >
              {DECISION_BUTTONS[decision].label}
            </Button>
          ))}
        </div>
      )}
    </form>
  );
}

function FieldNote({ id, error, children }: { id: string; error: string | undefined; children: ReactNode }) {
  return (
    <p id={id} className={cn("m-0 text-xs", error ? "font-semibold text-danger" : "text-muted")}>
      {error ?? children}
    </p>
  );
}
