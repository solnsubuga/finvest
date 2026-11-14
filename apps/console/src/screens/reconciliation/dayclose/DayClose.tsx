import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import type { Amount } from "../api.ts";
import { formatDay, formatExact } from "../format.ts";
import {
  BLOCKERS,
  DayCloseError,
  addDays,
  kampalaToday,
  KIND_LABELS,
  dayCloseErrorMessage,
  needsReload,
  type Day,
  type DayCloseApi,
  type Movement,
  type PotException,
  type PotLine,
  type Position,
  type StatementUpload,
} from "./api.ts";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

interface DayCloseProps {
  api: DayCloseApi;
  /** Whether the signed-in person holds `reconciliation.change`: import, pair and sign. Seeing needs less. */
  canAct: boolean;
  now?: () => Date;
}

/** The pots share a name per provider, so the account says which one. */
const ACCOUNT_LABELS: Record<string, string> = { mtn: "MTN", airtel: "Airtel", trust: "Stanbic trust" };

export function potTitle(position: Pick<Position, "name" | "accountReference">): string {
  const account = ACCOUNT_LABELS[position.accountReference] ?? position.accountReference;
  return `${position.name}, ${account}`;
}

/**
 * Closing a day: each pot's statement against the ledger at the end of the day, and what is still
 * open. One person prepares the close once every shilling is explained, and a different person,
 * who imported none of the day's statements, approves it. That closes the ledger through the day.
 */
export function DayClose({ api, canAct, now = () => new Date() }: DayCloseProps) {
  const yesterday = addDays(kampalaToday(now()), -1);
  const [date, setDate] = useState(yesterday);
  // Until the person picks a day, the screen moves to the first day not yet closed.
  const chosen = useRef(false);
  // Kept with the day it belongs to, so choosing another day shows neither.
  const [loaded, setLoaded] = useState<{ date: string; load: Load<Day> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchDay = useCallback(
    (day: string): Promise<Load<Day>> =>
      api.getDay(day).then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: dayCloseErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchDay(date).then((found) => {
      if (!current) return;
      if (!chosen.current && found.state === "ready" && found.value.lastClosed) {
        const next = addDays(found.value.lastClosed, 1);
        chosen.current = true;
        if (next < date) {
          setDate(next);
          return;
        }
      }
      setLoaded({ date, load: found });
    });
    return () => {
      current = false;
    };
  }, [fetchDay, date]);

  const load: Load<Day> = loaded?.date === date ? loaded.load : { state: "loading" };
  const reload = async () => setLoaded({ date, load: await fetchDay(date) });

  /** `done` is said once it worked, unless the day's own status says it already. */
  async function run(work: () => Promise<unknown>, done: string | null): Promise<boolean> {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      await work();
      setNotice(done);
      await reload();
      return true;
    } catch (error) {
      setFailure(dayCloseErrorMessage(error));
      if (needsReload(error)) await reload();
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function importStatement(upload: StatementUpload): Promise<DayCloseError | null> {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      const made = await api.importStatement(upload);
      setNotice(`Statement imported: ${made.lineCount === 1 ? "1 line" : `${made.lineCount} lines`}, paired with the ledger where the records and references allow.`);
      await api.refresh(upload.statementDate).catch(() => undefined);
      await reload();
      return null;
    } catch (error) {
      const refused = error instanceof DayCloseError ? error : new DayCloseError("unavailable");
      if (refused.code === "statement_already_imported") await reload();
      return refused;
    } finally {
      setBusy(false);
    }
  }

  const day = load.state === "ready" ? load.value : null;
  const ended = date < kampalaToday(now());
  const open = day !== null && day.close === null;

  return (
    <section aria-labelledby="dayclose-title" className="flex flex-col gap-4 border-t border-divider pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="dayclose-title" className="m-0 text-xl font-extrabold">
            Close the day
          </h2>
          <p className="m-0 text-md text-muted">
            Each pot's statement against the ledger at the end of the day. One person prepares the close and another approves it.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2.5">
          <DayPicker
            value={date}
            max={yesterday}
            onChange={(value) => {
              chosen.current = true;
              setFailure(null);
              setNotice(null);
              setDate(value);
            }}
          />
          {canAct && open && ended ? (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => void run(() => api.refresh(date), "The ledger's movements are in, and paired where the records and references allow.")}
            >
              Refresh the matches
            </Button>
          ) : null}
        </div>
      </div>

      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading {formatDay(date)}
        </p>
      ) : load.state === "failed" ? (
        <Failure message={load.message} onRetry={() => void reload()} />
      ) : (
        <>
          <CloseStatus
            day={load.value}
            ended={ended}
            canAct={canAct}
            busy={busy}
            failure={failure}
            notice={notice}
            onPrepare={() => void run(() => api.prepare(date), null)}
            onApprove={() => void run(() => api.approve(date), null)}
            onSendBack={(reason) => run(() => api.sendBack(date, reason), "Sent back. The day can be prepared again.")}
          />
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,420px),1fr))] items-start gap-4">
            {load.value.positions.map((position) => (
              <PotCard
                key={`${position.source}:${position.accountReference}`}
                date={date}
                position={position}
                editable={canAct && open && ended}
                busy={busy}
                onImport={importStatement}
                onMatch={(statementLineId, movement, reason) =>
                  run(() => api.match({ statementLineId, movement, reason }), "Paired. The difference is no longer open.")
                }
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function DayPicker({ value, max, onChange }: { value: string; max: string; onChange: (value: string) => void }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Business day</Label>
      <Input
        id={id}
        type="date"
        value={value}
        max={max}
        onChange={(event) => {
          if (event.target.value) onChange(event.target.value);
        }}
        className="w-[11rem] tabular-nums"
      />
    </div>
  );
}

function CloseStatus({
  day,
  ended,
  canAct,
  busy,
  failure,
  notice,
  onPrepare,
  onApprove,
  onSendBack,
}: {
  day: Day;
  ended: boolean;
  canAct: boolean;
  busy: boolean;
  failure: string | null;
  notice: string | null;
  onPrepare: () => void;
  onApprove: () => void;
  onSendBack: (reason: string) => Promise<boolean>;
}) {
  const { close } = day;
  const carried = day.positions.reduce((total, position) => total + position.openCount, 0);
  let tone: "success" | "warning" | "neutral" = "neutral";
  let title: string;
  let body: ReactNode;
  let actions: ReactNode = null;

  if (close?.status === "closed") {
    tone = "success";
    title = `${formatDay(day.date)} is closed`;
    body = `Two people signed it${close.decidedByYou ? ", and you approved it" : ""}. The ledger is closed through this day, so nothing more can be posted to it.${
      carried ? ` ${differences(carried)} ${carried === 1 ? "is" : "are"} carried to the next day.` : ""
    }`;
  } else if (close?.status === "prepared") {
    title = close.preparedByYou
      ? "Prepared by you, waiting for a second person"
      : canAct
        ? "Prepared, waiting for your approval"
        : "Prepared, waiting for a second person";
    body = close.preparedByYou
      ? "A different person, who imported none of this day's statements, has to approve it. You can send it back until they do."
      : canAct
        ? "Another member of staff prepared this close. Check each pot below. Approving closes the ledger through this day, and you will be asked to confirm it is you."
        : "Another member of staff prepared this close. Someone else who can change reconciliation, and who imported none of this day's statements, approves it.";
    if (canAct) actions = <Decide yours={close.preparedByYou} busy={busy} onApprove={onApprove} onSendBack={onSendBack} />;
  } else if (!ended) {
    title = "This day has not ended";
    body = "A day can be closed from the next morning in Kampala, once every statement for it is in.";
  } else if (day.blockers.length > 0) {
    tone = "warning";
    title = "Not ready to prepare";
    body = (
      <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
        {day.blockers.map((blocker) => (
          <li key={blocker}>{BLOCKERS[blocker]}</li>
        ))}
      </ul>
    );
  } else {
    title = "Ready to prepare";
    body = `Every pot's statement agrees with the ledger once the open differences are counted.${
      carried ? ` ${differences(carried)} will be carried to the next day until they are put right.` : ""
    } Preparing is the first of two signatures.`;
    if (canAct) {
      actions = (
        <Button disabled={busy} onClick={onPrepare} className="h-12 self-start px-6">
          {busy ? "Saving" : "Prepare the close"}
        </Button>
      );
    }
  }

  return (
    <div
      role="region"
      aria-label="Day close"
      className={cn(
        "flex flex-col gap-3 rounded-xl border p-5",
        tone === "success" ? "border-success bg-success-surface" : tone === "warning" ? "border-warning bg-warning-surface" : "border-border bg-surface",
      )}
    >
      <div className="flex flex-col gap-1">
        <h3 className={cn("m-0 text-lg font-extrabold", tone === "warning" ? "text-warning-text" : tone === "success" ? "text-success" : "")}>{title}</h3>
        <div className={cn("text-md leading-normal", tone === "warning" ? "text-warning-text" : "")}>{body}</div>
        {day.lastClosed && close?.status !== "closed" ? (
          <p className="m-0 text-xs text-muted">Last day closed: {formatDay(day.lastClosed)}</p>
        ) : null}
      </div>
      {!canAct && close?.status !== "closed" ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
          You can see this day. Importing statements, pairing lines and signing the close need the permission to change reconciliation.
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
          {notice}
        </p>
      ) : null}
      {failure ? <ErrorNote message={failure} /> : null}
      {actions}
    </div>
  );
}

function differences(count: number): string {
  return count === 1 ? "One open difference" : `${count} open differences`;
}

function Decide({
  yours,
  busy,
  onApprove,
  onSendBack,
}: {
  yours: boolean;
  busy: boolean;
  onApprove: () => void;
  onSendBack: (reason: string) => Promise<boolean>;
}) {
  const id = useId();
  const [sendingBack, setSendingBack] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  async function sendBack() {
    if (!reason.trim()) {
      setProblem(yours ? "Say why you are taking it back." : "Say why, so the person who prepared it knows what to check.");
      return;
    }
    setProblem(null);
    if (await onSendBack(reason.trim())) {
      setSendingBack(false);
      setReason("");
    }
  }

  if (sendingBack) {
    return (
      <div className="flex flex-col gap-3 border-t border-divider pt-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={id}>Why you are sending it back</Label>
          <textarea
            id={id}
            value={reason}
            maxLength={500}
            rows={3}
            onChange={(event) => {
              setReason(event.target.value);
              setProblem(null);
            }}
            className="rounded-md border border-border-strong bg-surface px-3 py-2 text-md font-medium"
          />
        </div>
        {problem ? <ErrorNote message={problem} /> : null}
        <div className="flex flex-wrap gap-2.5">
          <Button disabled={busy} onClick={() => void sendBack()} className="h-12 flex-[1_1_180px]">
            {busy ? "Saving" : "Send it back"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(false)} className="h-12 flex-[1_1_180px]">
            Go back
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-2.5">
      {yours ? null : (
        <Button disabled={busy} onClick={onApprove} className="h-12 flex-[1_1_180px]">
          {busy ? "Saving" : "Approve and close the day"}
        </Button>
      )}
      <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(true)} className={cn("h-12", yours ? "self-start px-6" : "flex-[1_1_180px]")}>
        {yours ? "Take it back" : "Send back"}
      </Button>
    </div>
  );
}

function PotCard({
  date,
  position,
  editable,
  busy,
  onImport,
  onMatch,
}: {
  date: string;
  position: Position;
  editable: boolean;
  busy: boolean;
  onImport: (upload: StatementUpload) => Promise<DayCloseError | null>;
  onMatch: (statementLineId: string, movement: Movement, reason: string) => Promise<boolean>;
}) {
  const title = potTitle(position);
  const unexplained = position.unexplained;
  const square = unexplained !== null && unexplained.minor === 0;
  const movements = position.exceptions.flatMap((each) => each.movements);
  return (
    <article aria-label={title} className="flex min-w-0 flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="m-0 text-lg font-extrabold">{title}</h3>
        <PotPill position={position} />
      </header>
      <dl className="m-0">
        <Row name="Statement closing" value={position.statementClosing ? formatExact(position.statementClosing) : "Not imported"} />
        <Row name="Ledger closing" value={formatExact(position.ledgerClosing)} />
        <Row
          name="Open differences"
          value={position.openCount === 0 ? "None" : `${position.openCount} · ${formatSigned(position.openTotal)}`}
        />
        <Row
          name="Unexplained"
          value={
            unexplained === null ? (
              <span className="text-muted">Needs the statement</span>
            ) : (
              <span className={square ? "text-success" : "text-danger"}>{square ? "Nothing" : formatSigned(unexplained)}</span>
            )
          }
        />
      </dl>

      {position.statementClosing === null && editable ? <ImportForm date={date} position={position} busy={busy} onImport={onImport} /> : null}

      {position.exceptions.length > 0 ? (
        <Section title="Still open">
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {position.exceptions.map((exception, index) => (
              <li key={`${exception.kind}:${exception.lines[0]?.id ?? exception.movements[0]?.id ?? index}`}>
                <ExceptionCard exception={exception} movements={movements} editable={editable} busy={busy} onMatch={onMatch} />
              </li>
            ))}
          </ul>
        </Section>
      ) : position.statementClosing !== null ? (
        <p className="m-0 text-md text-muted">Every line on the statement is paired with the ledger.</p>
      ) : null}
    </article>
  );
}

function PotPill({ position }: { position: Position }) {
  const [label, tone] =
    position.statementClosing === null
      ? ["Statement missing", "bg-warning-surface text-warning-text"]
      : position.unexplained?.minor !== 0
        ? ["Does not agree", "bg-danger-surface text-danger"]
        : position.openCount > 0
          ? ["Explained", "bg-surface-sunken text-muted"]
          : ["Agrees", "bg-success-surface text-success"];
  return <span className={cn("shrink-0 rounded-pill px-2.5 py-1 text-xs font-bold", tone)}>{label}</span>;
}

/** A difference with its sign: how far it puts the statement above (+) or below (−) the ledger. */
function formatSigned(amount: Amount): string {
  if (amount.minor === 0) return formatExact(amount);
  return amount.minor > 0 ? `+${formatExact(amount)}` : `−${formatExact({ ...amount, minor: -amount.minor })}`;
}

function ExceptionCard({
  exception,
  movements,
  editable,
  busy,
  onMatch,
}: {
  exception: PotException;
  movements: Movement[];
  editable: boolean;
  busy: boolean;
  onMatch: (statementLineId: string, movement: Movement, reason: string) => Promise<boolean>;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-md font-bold">{KIND_LABELS[exception.kind]}</span>
        <span className="text-md font-extrabold tabular-nums">{formatSigned(exception.difference)}</span>
      </div>
      {exception.lines.map((line) => (
        <div key={line.id} className="flex flex-col gap-2">
          <LineSummary line={line} />
          {editable && line.suggestedMovements.length > 0 ? (
            <PairForm line={line} options={movements.filter((each) => line.suggestedMovements.includes(each.id))} busy={busy} onMatch={onMatch} />
          ) : null}
        </div>
      ))}
      {exception.movements.map((movement) => (
        <MovementSummary key={movement.id} movement={movement} />
      ))}
    </div>
  );
}

function LineSummary({ line }: { line: PotLine }) {
  return (
    <div className="flex flex-col gap-0.5 text-md">
      <span className="text-xs font-bold tracking-[0.6px] text-muted uppercase">Statement</span>
      <span>
        <strong className="tabular-nums">
          {line.direction === "in" ? "In" : "Out"} {formatExact(line.amount)}
        </strong>{" "}
        on {formatDay(line.valueDate)}
      </span>
      <span className="break-words text-muted">
        {line.counterpartyHint} · <span className="tabular-nums">{line.reference || "No reference"}</span>
      </span>
    </div>
  );
}

/** What the ledger entry was for, from its type, such as `payment.deposit_confirmed`. */
export function entryLabel(entryType: string): string {
  if (entryType === "ledger.reversal") return "Reversal";
  if (entryType.startsWith("ledger.correction")) return "Correction";
  if (entryType === "payment.reassignment") return "Payment moved to another client";
  const [module] = entryType.split(".");
  if (module === "payment") return "Client payment";
  if (module === "payout") return "Payout";
  if (module === "tax") return "Tax paid to URA";
  if (module === "investment") return "Investment";
  return entryType;
}

function MovementSummary({ movement }: { movement: Movement }) {
  return (
    <div className="flex flex-col gap-0.5 text-md">
      <span className="text-xs font-bold tracking-[0.6px] text-muted uppercase">Ledger</span>
      <span>
        <strong className="tabular-nums">
          {movement.direction === "in" ? "In" : "Out"} {formatExact(movement.amount)}
        </strong>{" "}
        on {formatDay(movement.effectiveDate)}
      </span>
      <span className="break-words text-muted">
        {entryLabel(movement.entryType)} ·{" "}
        <span className="tabular-nums">{movement.reference}</span>
      </span>
    </div>
  );
}

function PairForm({
  line,
  options,
  busy,
  onMatch,
}: {
  line: PotLine;
  options: Movement[];
  busy: boolean;
  onMatch: (statementLineId: string, movement: Movement, reason: string) => Promise<boolean>;
}) {
  const ids = useId();
  const [opened, setOpened] = useState(false);
  const [picked, setPicked] = useState(options.length === 1 ? (options[0]?.id ?? "") : "");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  if (!opened) {
    return (
      <Button variant="secondary" disabled={busy} onClick={() => setOpened(true)} className="self-start">
        Pair by hand
      </Button>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const movement = options.find((each) => each.id === picked);
    if (!movement) {
      setProblem("Choose the ledger movement this line is.");
      return;
    }
    if (!reason.trim()) {
      setProblem("Say how you know they are the same money, for the person who approves the close.");
      return;
    }
    setProblem(null);
    await onMatch(line.id, movement, reason.trim());
  }

  return (
    <form aria-label="Pair by hand" onSubmit={(event) => void submit(event)} className="flex flex-col gap-3 rounded-md bg-surface-sunken p-3.5">
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 text-md font-bold">Ledger movements of the same amount within three days</legend>
        {options.map((movement) => (
          <label key={movement.id} className="flex items-start gap-2.5 rounded-md border border-border bg-surface px-3 py-2.5">
            <input
              type="radio"
              name={`${ids}-movement`}
              value={movement.id}
              checked={picked === movement.id}
              onChange={() => {
                setPicked(movement.id);
                setProblem(null);
              }}
              className="mt-1"
            />
            <MovementSummary movement={movement} />
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-reason`}>How you know they are the same money</Label>
        <textarea
          id={`${ids}-reason`}
          value={reason}
          maxLength={500}
          rows={2}
          onChange={(event) => {
            setReason(event.target.value);
            setProblem(null);
          }}
          className="rounded-md border border-border-strong bg-surface px-3 py-2 text-md font-medium"
        />
      </div>
      {problem ? <ErrorNote message={problem} /> : null}
      <div className="flex flex-wrap gap-2.5">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving" : "Pair them"}
        </Button>
        <Button type="button" variant="secondary" disabled={busy} onClick={() => setOpened(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

type UploadValues = { openingBalance: string; closingBalance: string };

function ImportForm({
  date,
  position,
  busy,
  onImport,
}: {
  date: string;
  position: Position;
  busy: boolean;
  onImport: (upload: StatementUpload) => Promise<DayCloseError | null>;
}) {
  const ids = useId();
  const [opened, setOpened] = useState(false);
  const [values, setValues] = useState<UploadValues>({ openingBalance: "", closingBalance: "" });
  const [file, setFile] = useState<File | null>(null);
  const [problems, setProblems] = useState<Partial<Record<keyof UploadValues | "file" | "form", string | undefined>>>({});

  if (!opened) {
    return (
      <div className="flex flex-col items-start gap-2 rounded-md bg-warning-surface px-3.5 py-3">
        <p className="m-0 text-md leading-normal text-warning-text">This pot's statement for {formatDay(date)} has not been imported.</p>
        <Button variant="secondary" disabled={busy} onClick={() => setOpened(true)}>
          Import the statement
        </Button>
      </div>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const missing: typeof problems = {};
    if (!values.openingBalance.trim()) missing.openingBalance = "Enter the opening balance the statement prints.";
    if (!values.closingBalance.trim()) missing.closingBalance = "Enter the closing balance the statement prints.";
    if (!file) missing.file = "Choose the statement's CSV file.";
    setProblems(missing);
    if (Object.keys(missing).length > 0 || !file) return;
    const error = await onImport({
      source: position.source,
      accountReference: position.accountReference,
      statementDate: date,
      openingBalance: values.openingBalance.trim(),
      closingBalance: values.closingBalance.trim(),
      file,
    });
    if (!error) return;
    const message = dayCloseErrorMessage(error);
    setProblems(error.field ? { [error.field]: message } : { form: message });
  }

  const balance = (name: keyof UploadValues, label: string) => (
    <div className="flex min-w-0 flex-[1_1_160px] flex-col gap-1.5">
      <Label htmlFor={`${ids}-${name}`}>{label}</Label>
      <Input
        id={`${ids}-${name}`}
        inputMode="numeric"
        placeholder="1,500,000"
        value={values[name]}
        aria-invalid={problems[name] ? true : undefined}
        onChange={(event) => {
          setValues({ ...values, [name]: event.target.value });
          setProblems({ ...problems, [name]: undefined, form: undefined });
        }}
        className="tabular-nums"
      />
      {problems[name] ? <p className="m-0 text-xs font-semibold text-danger">{problems[name]}</p> : null}
    </div>
  );

  return (
    <form aria-label={`Import the statement for ${potTitle(position)}`} onSubmit={(event) => void submit(event)} className="flex flex-col gap-3 rounded-md bg-surface-sunken p-3.5">
      <p className="m-0 text-md leading-normal">
        The statement for {formatDay(date)}, as a CSV with the columns Date (YYYY-MM-DD), Details, Reference, In and Out. The whole file is
        imported or none of it.
      </p>
      <div className="flex flex-wrap gap-3">
        {balance("openingBalance", "Opening balance")}
        {balance("closingBalance", "Closing balance")}
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-file`}>Statement file</Label>
        <input
          id={`${ids}-file`}
          type="file"
          accept=".csv,text/csv"
          aria-invalid={problems.file ? true : undefined}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setProblems({ ...problems, file: undefined, form: undefined });
          }}
          className="text-md"
        />
        {problems.file ? <p className="m-0 text-xs font-semibold text-danger">{problems.file}</p> : null}
      </div>
      {problems.form ? <ErrorNote message={problems.form} /> : null}
      <div className="flex flex-wrap gap-2.5">
        <Button type="submit" disabled={busy}>
          {busy ? "Importing" : "Import"}
        </Button>
        <Button type="button" variant="secondary" disabled={busy} onClick={() => setOpened(false)}>
          Cancel
        </Button>
      </div>
      <p className="m-0 text-xs text-muted">Whoever imports one of the day's statements cannot approve its close.</p>
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

function Failure({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h3 className="m-0 text-lg font-extrabold">The day could not be loaded</h3>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-2">
      <h4 id={id} className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Row({ name, value }: { name: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-divider py-2.5 text-md">
      <dt className="shrink-0 text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold tabular-nums">{value}</dd>
    </div>
  );
}
