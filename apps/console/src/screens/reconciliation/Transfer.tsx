import { useId, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import type { RefuseReason, StatementLine, TransferDetail } from "./api.ts";
import { REFUSE_REASONS, STAGE_LABELS, daysApart, formatDay, formatExact, formatMoment, formatSize } from "./format.ts";

export type TransferCommand =
  | { action: "match"; statementLineId: string }
  | { action: "refuse"; reason: RefuseReason }
  | { action: "approve" };

interface TransferProps {
  transfer: TransferDetail;
  /** Whether this person holds the permission to decide transfers. Seeing them needs less. */
  canDecide: boolean;
  busy: boolean;
  error: string | null;
  onCommand: (command: TransferCommand) => void;
}

/** One transfer: the receipt beside what the bank statement says, and the step that is next. */
export function Transfer({ transfer, canDecide, busy, error, onCommand }: TransferProps) {
  return (
    <article aria-label={`Transfer of ${formatExact(transfer.amount)}`} className="flex flex-col gap-5 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="m-0 text-2xl font-extrabold tabular-nums">{formatExact(transfer.amount)}</h2>
          <p className="m-0 text-md text-muted">
            For one investment, told to us {formatMoment(transfer.submittedAt)}
          </p>
        </div>
        <StagePill transfer={transfer} />
      </header>

      <div className="flex flex-wrap items-start gap-5">
        <ReceiptView transfer={transfer} />
        <div className="flex min-w-0 flex-[1_1_280px] flex-col gap-5">
          <Section title="What the client told us">
            <dl className="m-0">
              <Row name="Amount sent" value={formatExact(transfer.amount)} />
              <Row name="Day sent" value={formatDay(transfer.sentOn)} />
              <Row name="From" value={transfer.bankName} />
              <Row name="Bank's transaction number" value={<span className="tabular-nums">{transfer.transactionNumber}</span>} />
            </dl>
          </Section>
          {transfer.matchedLine ? (
            <Section title="Matched statement line">
              <LineFacts line={transfer.matchedLine} sentOn={transfer.sentOn} />
            </Section>
          ) : null}
        </div>
      </div>

      <Outcome transfer={transfer} />
      {!canDecide && transfer.stage !== "confirmed" && transfer.stage !== "refused" ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
          You can see this transfer. Matching, refusing and approving need the bank transfers permission.
        </p>
      ) : null}
      {canDecide && transfer.allowed.includes("match") ? (
        <MatchForm key={transfer.id} transfer={transfer} busy={busy} error={error} onCommand={onCommand} />
      ) : null}
      {canDecide && transfer.allowed.includes("approve") ? (
        <ApproveForm transfer={transfer} busy={busy} error={error} onCommand={onCommand} />
      ) : null}
      {!transfer.allowed.length && error ? <ErrorNote message={error} /> : null}
    </article>
  );
}

function StagePill({ transfer }: { transfer: TransferDetail }) {
  const tone =
    transfer.stage === "confirmed"
      ? "bg-success-surface text-success"
      : transfer.stage === "refused"
        ? "bg-danger-surface text-danger"
        : transfer.stage === "to_match"
          ? "bg-warning-surface text-warning-text"
          : "bg-surface-sunken text-muted";
  return <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", tone)}>{STAGE_LABELS[transfer.stage]}</span>;
}

function ReceiptView({ transfer }: { transfer: TransferDetail }) {
  const { receipt } = transfer;
  const isImage = receipt.contentType.startsWith("image/");
  return (
    <figure className="m-0 flex min-w-0 flex-[1_1_280px] flex-col gap-2">
      <div className="flex aspect-[21/22] w-full items-center justify-center overflow-hidden rounded-lg border border-border bg-surface-sunken">
        {receipt.url === null ? (
          <p className="m-0 max-w-[260px] p-4 text-center text-md leading-normal text-muted">
            The receipt cannot be shown right now. Reload the transfer to try again.
          </p>
        ) : isImage ? (
          <img src={receipt.url} alt="The client's receipt" className="size-full object-contain" />
        ) : (
          <iframe
            src={receipt.url}
            title="The client's receipt"
            sandbox=""
            referrerPolicy="no-referrer"
            className="size-full border-0 bg-white"
          />
        )}
      </div>
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span>
          Receipt, {receipt.contentType === "application/pdf" ? "PDF" : receipt.contentType === "image/png" ? "PNG" : "JPEG"},{" "}
          {formatSize(receipt.byteSize)}
        </span>
        {receipt.url ? (
          <a href={receipt.url} target="_blank" rel="noreferrer" className="font-bold text-ink underline">
            Open full size
          </a>
        ) : null}
      </figcaption>
    </figure>
  );
}

function LineFacts({ line, sentOn }: { line: StatementLine; sentOn: string }) {
  return (
    <dl className="m-0">
      <Row name="Amount in" value={formatExact(line.amount)} />
      <Row name="Value date" value={`${formatDay(line.valueDate)}`} note={daysApart(sentOn, line.valueDate)} />
      <Row name="Bank's description" value={line.narrative} />
      <Row name="Reference" value={<span className="tabular-nums">{line.reference}</span>} />
      <Row name="Statement" value={line.accountReference} />
    </dl>
  );
}

/** What has happened to a transfer that is not waiting for this person. */
function Outcome({ transfer }: { transfer: TransferDetail }) {
  const { review } = transfer;
  let text: ReactNode = null;
  if (transfer.stage === "confirmed") {
    text = "Confirmed. The money is recorded as received and the investment starts.";
  } else if (transfer.stage === "confirming") {
    text = "Approved. Finvest is recording the money now. This takes a moment.";
  } else if (transfer.stage === "refused" && review?.reason) {
    text = (
      <>
        Refused: {REFUSE_REASONS[review.reason].toLowerCase()}. No money was recorded, and the client can pay again.
      </>
    );
  } else if (transfer.stage === "to_approve" && review?.reviewedByYou) {
    text = (
      <>
        <strong>You matched this {formatMoment(review.reviewedAt)}.</strong> A second person has to approve it, so it waits for
        someone else with the bank transfers permission.
      </>
    );
  } else if (transfer.stage === "to_approve" && review) {
    text = `Another member of staff matched this to the line above, ${formatMoment(review.reviewedAt)}. Check it against the receipt before you approve.`;
  }
  if (!text) return null;
  return (
    <p role="status" className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
      {text}
    </p>
  );
}

interface FormProps {
  transfer: TransferDetail;
  busy: boolean;
  error: string | null;
  onCommand: (command: TransferCommand) => void;
}

function MatchForm({ transfer, busy, error, onCommand }: FormProps) {
  const ids = useId();
  const [chosen, setChosen] = useState<string | null>(null);
  const [refusing, setRefusing] = useState(false);
  const [reason, setReason] = useState<RefuseReason | "">(transfer.candidates.length === 0 ? "not_on_statement" : "");
  const [problem, setProblem] = useState<string | null>(null);

  function match() {
    if (!chosen) {
      setProblem("Choose the statement line that is this transfer.");
      return;
    }
    setProblem(null);
    onCommand({ action: "match", statementLineId: chosen });
  }

  function refuse() {
    if (!reason) {
      setProblem("Choose why it cannot be matched.");
      return;
    }
    setProblem(null);
    onCommand({ action: "refuse", reason });
  }

  return (
    <form aria-label="Match to the bank statement" className="flex flex-col gap-4 border-t border-divider pt-4" onSubmit={(event) => event.preventDefault()}>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 p-0 text-lg font-extrabold">Find it on the bank statement</legend>
        {transfer.candidates.length === 0 ? (
          <p className="m-0 rounded-md bg-warning-surface px-3.5 py-3 text-md leading-normal text-warning-text">
            No line on the imported bank statements is money in for {formatExact(transfer.amount)} within 7 days of{" "}
            {formatDay(transfer.sentOn)}. If today's statement is not imported yet, wait for it. Otherwise refuse the transfer.
          </p>
        ) : (
          <>
            <p className="m-0 text-md text-muted">
              Lines with money in for exactly {formatExact(transfer.amount)} within 7 days of the day sent. Pick the one whose
              description matches the receipt.
            </p>
            {transfer.candidates.map((line) => (
              <label
                key={line.id}
                className={cn(
                  "flex cursor-pointer gap-3 rounded-lg border px-4 py-3",
                  chosen === line.id ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
                )}
              >
                <input
                  type="radio"
                  name={`${ids}-line`}
                  value={line.id}
                  checked={chosen === line.id}
                  onChange={() => {
                    setChosen(line.id);
                    setRefusing(false);
                    setProblem(null);
                  }}
                  className="mt-1 size-4 shrink-0 accent-black"
                />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-md font-bold">{line.narrative}</span>
                  <span className="text-xs text-muted">
                    {formatDay(line.valueDate)} ({daysApart(transfer.sentOn, line.valueDate).toLowerCase()}), reference{" "}
                    <span className="tabular-nums">{line.reference}</span>, {line.accountReference}
                  </span>
                </span>
              </label>
            ))}
          </>
        )}
      </fieldset>

      {refusing || transfer.candidates.length === 0 ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-reason`}>Why it cannot be matched</Label>
          <select
            id={`${ids}-reason`}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value as RefuseReason | "");
              setProblem(null);
            }}
            className="h-11 rounded-md border border-border-strong bg-surface px-3 text-md font-medium"
          >
            <option value="">Choose a reason</option>
            {(Object.keys(REFUSE_REASONS) as RefuseReason[]).map((key) => (
              <option key={key} value={key}>
                {REFUSE_REASONS[key]}
              </option>
            ))}
          </select>
          <p className="m-0 text-xs text-muted">The payment fails with no money recorded. The client is told and can pay again.</p>
        </div>
      ) : null}

      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}

      <div className="flex flex-wrap gap-2.5">
        {transfer.candidates.length > 0 && !refusing ? (
          <>
            <Button disabled={busy} onClick={match} className="h-12 flex-[1_1_180px]">
              {busy ? "Saving" : "Match to this line"}
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setRefusing(true);
                setChosen(null);
                setProblem(null);
              }}
              className="h-12 flex-[1_1_180px]"
            >
              It is not on the statement
            </Button>
          </>
        ) : (
          <>
            <Button disabled={busy} onClick={refuse} className="h-12 flex-[1_1_180px]">
              {busy ? "Saving" : "Refuse the transfer"}
            </Button>
            {transfer.candidates.length > 0 ? (
              <Button variant="secondary" disabled={busy} onClick={() => setRefusing(false)} className="h-12 flex-[1_1_180px]">
                Go back to the lines
              </Button>
            ) : null}
          </>
        )}
      </div>
      <p className="m-0 text-xs text-muted">A match is not final: a second person checks it and approves before any money is recorded.</p>
    </form>
  );
}

function ApproveForm({ busy, error, onCommand }: FormProps) {
  return (
    <form aria-label="Approve the match" className="flex flex-col gap-3 border-t border-divider pt-4" onSubmit={(event) => event.preventDefault()}>
      <p className="m-0 text-md leading-normal">
        Approve only if the amount, the day and the sender on the receipt agree with the statement line. Approving records the money
        as received and starts the investment.
      </p>
      {error ? <ErrorNote message={error} /> : null}
      <Button disabled={busy} onClick={() => onCommand({ action: "approve" })} className="h-12 self-start px-6">
        {busy ? "Saving" : "Approve as second person"}
      </Button>
    </form>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
      {message}
    </p>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="min-w-0">
      <h3 id={id} className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Row({ name, value, note }: { name: string; value: ReactNode; note?: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-divider py-2.5 text-md">
      <dt className="shrink-0 text-muted">{name}</dt>
      <dd className="m-0 flex flex-col items-end text-right font-bold">
        {value}
        {note ? <span className="text-xs font-medium text-muted">{note}</span> : null}
      </dd>
    </div>
  );
}
