import { useCallback, useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { DoneNote, Empty, ErrorNote, Failure } from "@/screens/money-movement/Decide.tsx";
import { useCommandKeys, useLoad, type Load } from "@/screens/money-movement/load.ts";
import { formatDay, formatExact, formatMoment, formatSize } from "../format.ts";
import { parseAmount } from "../transfers/PotTransfers.tsx";
import {
  CustodianError,
  custodianErrorMessage,
  failureMessage,
  type CheckDetail,
  type CheckItem,
  type CheckStatus,
  type CheckSummary,
  type Correction,
  type CustodianChecksApi,
  type StatementRow,
} from "./api.ts";

interface CustodianChecksProps {
  api: CustodianChecksApi;
  /** Whether this person holds `reconciliation.change`: uploading, reviewing and signing off. */
  canChange: boolean;
  newKey?: () => string;
  /** Today in Kampala, `YYYY-MM-DD`, the latest day a statement can be for. */
  today?: () => string;
  /** The check to open first, such as one named in the address. */
  initialCheck?: string | null;
  /** How often to look again while a statement is being read. */
  pollMs?: number;
}

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

const STATUS: Record<CheckStatus, { label: string; tone: "neutral" | "warning" | "danger" | "success" }> = {
  reading: { label: "Being read", tone: "neutral" },
  unreadable: { label: "Could not be read", tone: "danger" },
  to_review: { label: "To review", tone: "warning" },
  waiting_sign_off: { label: "Waiting for sign-off", tone: "warning" },
  signed_off: { label: "Signed off", tone: "success" },
};

const KIND: Record<CheckItem["kind"], string> = {
  match: "Matches",
  amount_differs: "Amounts differ",
  missing_at_custodian: "Not on the statement",
  not_in_finvest: "Not in Finvest's records",
  row_unreadable: "Line could not be counted",
  statement_date_differs: "Statement is for another day",
  statement_date_unread: "Statement shows no date",
};

const PROBLEM: Record<string, string> = {
  row_amount_unreadable: "Amount could not be read exactly",
  row_maturity_unreadable: "No maturity date could be read",
  row_currency_unknown: "Currency not known",
};

const REFRESH: ReadonlySet<string> = new Set(["not_found", "refused", "check_not_in_review", "check_not_waiting", "differences_unexplained"]);

const money = (minor: number, currency: CheckItem["currency"]) => formatExact({ minor, currency: currency ?? "UGX" });

/** The custodian's holdings statement, read by AI and checked by two people. */
export function CustodianChecks({
  api,
  canChange,
  newKey = () => crypto.randomUUID(),
  today = kampalaToday,
  initialCheck = null,
  pollMs = 5000,
}: CustodianChecksProps) {
  const [list, reloadList] = useLoad(
    useCallback(() => api.list(), [api]),
    custodianErrorMessage,
  );
  const [chosen, setChosen] = useState<string | null>(initialCheck);
  const [detail, setDetail] = useState<{ id: string; load: Load<CheckDetail> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "done" | "failed"; message: string } | null>(null);
  const keys = useCommandKeys(newKey);

  const checks = list.state === "ready" ? list.value.checks : [];
  const open = checks.find((each) => each.status === "to_review" || each.status === "waiting_sign_off");
  const selectedId = chosen ?? open?.id ?? checks[0]?.id ?? null;

  const loadDetail = useCallback(
    (id: string) =>
      api.get(id).then(
        (value): Load<CheckDetail> => ({ state: "ready", value }),
        (error: unknown): Load<CheckDetail> => ({ state: "failed", message: custodianErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    if (!selectedId) return;
    let current = true;
    void loadDetail(selectedId).then((load) => current && setDetail({ id: selectedId, load }));
    return () => {
      current = false;
    };
  }, [loadDetail, selectedId]);

  const shown: Load<CheckDetail> = detail?.id === selectedId ? detail.load : { state: "loading" };
  const reading = shown.state === "ready" && shown.value.check.status === "reading";

  // While the worker reads the statement, look again every few seconds.
  useEffect(() => {
    if (!reading || !selectedId) return;
    const timer = setInterval(() => {
      void loadDetail(selectedId).then((load) => {
        setDetail({ id: selectedId, load });
        if (load.state === "ready" && load.value.check.status !== "reading") void reloadList();
      });
    }, pollMs);
    return () => clearInterval(timer);
  }, [reading, selectedId, loadDetail, reloadList, pollMs]);

  async function run(request: unknown, command: (key: string) => Promise<CheckDetail | CheckSummary>, done: string) {
    setBusy(true);
    setNote(null);
    const key = keys.keyFor(JSON.stringify(request));
    try {
      const result = await command(key);
      keys.settle();
      setNote({ kind: "done", message: done });
      const id = "check" in result ? result.check.id : result.id;
      if ("check" in result) setDetail({ id, load: { state: "ready", value: result } });
      setChosen(id);
      await reloadList();
    } catch (error) {
      if (error instanceof CustodianError && error.code !== "unavailable") keys.settle();
      setNote({ kind: "failed", message: custodianErrorMessage(error) });
      if (error instanceof CustodianError && REFRESH.has(error.code) && selectedId) {
        setDetail({ id: selectedId, load: await loadDetail(selectedId) });
        await reloadList();
      }
    } finally {
      setBusy(false);
    }
  }

  const readingOn = list.state === "ready" && list.value.readingOn;

  return (
    <section aria-labelledby="custodian-checks" className="flex flex-col gap-4">
      <div>
        <h2 id="custodian-checks" className="m-0 text-xl font-extrabold">
          Custodian statement
        </h2>
        <p className="m-0 max-w-[760px] text-md text-muted">
          The bills the custodian holds for Finvest, checked against the dealing desk's records. AI copies each holding off the statement, in any layout.
          Finvest's own figures do the comparing. You check every line against the original, and a different person signs it off.
        </p>
      </div>
      {note?.kind === "done" ? <DoneNote message={note.message} /> : null}

      {list.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading custodian checks
        </p>
      ) : list.state === "failed" ? (
        <Failure title="Custodian checks could not be loaded" message={list.message} onRetry={() => void reloadList()} />
      ) : (
        <>
          {canChange ? (
            readingOn ? (
              <Upload
                busy={busy}
                today={today()}
                error={note?.kind === "failed" && !selectedId ? note.message : null}
                onUpload={(statementDate, file) =>
                  void run(
                    { upload: statementDate, name: file.name, size: file.size, modified: file.lastModified },
                    (key) => api.upload(key, statementDate, file),
                    "Uploaded. The statement is being read; it usually takes under a minute.",
                  )
                }
              />
            ) : (
              <p className="m-0 max-w-[760px] rounded-md bg-warning-surface px-3.5 py-3 text-md text-warning-text">
                Reading custodian statements is not set up on this Finvest yet, so nothing can be uploaded. Past checks stay below.
              </p>
            )
          ) : null}

          {checks.length === 0 ? (
            <Empty>No custodian statement has been checked yet. Upload the latest statement to compare it with the dealing desk's bills.</Empty>
          ) : (
            <div className="flex flex-wrap items-start gap-5">
              <ul aria-label="Custodian checks" className="m-0 flex min-w-0 flex-[1_1_240px] list-none flex-col gap-2 p-0">
                {checks.map((check) => (
                  <li key={check.id}>
                    <button
                      type="button"
                      aria-current={check.id === selectedId ? "true" : undefined}
                      onClick={() => {
                        setChosen(check.id);
                        setNote(null);
                        keys.settle();
                      }}
                      className={cn(
                        "flex w-full flex-col items-start gap-1 rounded-xl border bg-surface p-3.5 text-left",
                        check.id === selectedId ? "border-ink" : "border-border",
                      )}
                    >
                      <span className="text-md font-bold">Holdings at {formatDay(check.statementDate)}</span>
                      <StatusPill status={check.status} />
                      <span className="text-sm text-muted">Uploaded {formatMoment(check.uploadedAt)}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="min-w-0 flex-[3_1_560px]">
                {shown.state === "loading" ? (
                  <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
                    Loading this check
                  </p>
                ) : shown.state === "failed" ? (
                  <ErrorNote message={shown.message} />
                ) : (
                  <Check
                    key={`${shown.value.check.id}:${shown.value.check.status}`}
                    detail={shown.value}
                    canChange={canChange}
                    busy={busy}
                    error={note?.kind === "failed" ? note.message : null}
                    onOpenFile={() =>
                      void api.fileLink(shown.value.check.id).then(
                        (url) => window.open(url, "_blank", "noopener,noreferrer"),
                        (error: unknown) => setNote({ kind: "failed", message: custodianErrorMessage(error) }),
                      )
                    }
                    onCorrect={(rowId, correction) =>
                      void run({ rowId, correction }, () => api.correctRow(shown.value.check.id, rowId, correction), "Saved. The comparison was worked out again.")
                    }
                    onNote={(itemKey, text) => void run({ itemKey, text }, () => api.note(shown.value.check.id, itemKey, text), "Note saved.")}
                    onSubmit={(text) =>
                      void run(
                        { submit: shown.value.check.id, text },
                        (key) => api.submit(key, shown.value.check.id, text),
                        "Sent. A different person must now sign it off.",
                      )
                    }
                    onSignOff={(text) =>
                      void run({ signOff: shown.value.check.id, text }, (key) => api.signOff(key, shown.value.check.id, text), "Signed off.")
                    }
                    onSendBack={(text) =>
                      void run(
                        { sendBack: shown.value.check.id, text },
                        (key) => api.sendBack(key, shown.value.check.id, text),
                        "Sent back for more review.",
                      )
                    }
                  />
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function StatusPill({ status }: { status: CheckStatus }) {
  const { label, tone } = STATUS[status];
  return (
    <span
      className={cn(
        "rounded-full px-2.5 py-0.5 text-sm font-bold",
        tone === "warning" && "bg-warning-surface text-warning-text",
        tone === "danger" && "bg-danger-surface text-danger",
        tone === "success" && "bg-success-surface text-success",
        tone === "neutral" && "bg-surface-sunken text-ink",
      )}
    >
      {label}
    </span>
  );
}

function Upload({
  busy,
  today,
  error,
  onUpload,
}: {
  busy: boolean;
  today: string;
  error: string | null;
  onUpload: (statementDate: string, file: File) => void;
}) {
  const id = useId();
  const [statementDate, setStatementDate] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      aria-label="Upload a custodian statement"
      className="flex max-w-[760px] flex-col gap-3 rounded-xl border border-border bg-surface p-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (!statementDate) return setProblem("Enter the day the statement states holdings at.");
        if (statementDate > today) return setProblem("The statement date cannot be after today.");
        if (!file) return setProblem("Choose the statement file.");
        if (file.size > 10 * 1024 * 1024) return setProblem("Choose a file of at most 10 MB.");
        setProblem(null);
        onUpload(statementDate, file);
      }}
    >
      <h3 className="m-0 text-lg font-extrabold">Upload a statement</h3>
      <div className="flex flex-wrap gap-3">
        <div className="flex min-w-0 flex-[1_1_180px] flex-col gap-1.5">
          <Label htmlFor={`${id}-date`}>Holdings at</Label>
          <Input id={`${id}-date`} type="date" max={today} value={statementDate} onChange={(event) => setStatementDate(event.target.value)} />
        </div>
        <div className="flex min-w-0 flex-[2_1_260px] flex-col gap-1.5">
          <Label htmlFor={`${id}-file`}>Statement file</Label>
          <Input
            id={`${id}-file`}
            type="file"
            accept=".pdf,.xlsx,.csv,.png,.jpg,.jpeg,application/pdf,text/csv,image/png,image/jpeg"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </div>
      </div>
      <p className="m-0 text-sm text-muted">
        PDF, Excel (.xlsx), CSV or a photo, up to 10 MB. The statement goes to Anthropic's Claude to be read; it is kept here as uploaded.
      </p>
      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
      <Button type="submit" disabled={busy} className="h-12 self-start px-6">
        {busy ? "Uploading" : "Upload and read"}
      </Button>
    </form>
  );
}

interface CheckProps {
  detail: CheckDetail;
  /** The backend's `allowed` is the rule; this only hides what the role cannot do anyway. */
  canChange: boolean;
  busy: boolean;
  error: string | null;
  onOpenFile: () => void;
  onCorrect: (rowId: string, correction: Correction) => void;
  onNote: (itemKey: string, note: string) => void;
  onSubmit: (note: string | null) => void;
  onSignOff: (note: string | null) => void;
  onSendBack: (reason: string) => void;
}

function Check({ detail, canChange, busy, error, onOpenFile, onCorrect, onNote, onSubmit, onSignOff, onSendBack }: CheckProps) {
  const { check, rows, items } = detail;
  const may = (action: CheckSummary["allowed"][number]) => canChange && check.allowed.includes(action);
  const differences = items.filter((item) => item.kind !== "match");
  const matches = items.filter((item) => item.kind === "match");
  return (
    <article aria-label={`Holdings at ${formatDay(check.statementDate)}`} className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="m-0 text-lg font-extrabold">Holdings at {formatDay(check.statementDate)}</h3>
          <p className="m-0 text-sm text-muted">
            {[check.custodianName, check.accountReference].filter(Boolean).join(" · ") || "Custodian not named on the statement"}
            {" · "}
            {formatSize(check.fileBytes)} · uploaded {formatMoment(check.uploadedAt)}
          </p>
          {check.reader ? <p className="m-0 text-sm text-muted">{check.reader.startsWith("claude") ? "Read by Claude" : "Read by the sample reader"}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          <StatusPill status={check.status} />
          <Button variant="secondary" onClick={onOpenFile}>
            Open the original
          </Button>
        </div>
      </header>

      {check.status === "reading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Reading the statement. This page looks again every few seconds.
        </p>
      ) : check.status === "unreadable" ? (
        <ErrorNote message={`${failureMessage(check.failureCode)} Upload the statement again, or as another file type.`} />
      ) : (
        <>
          {check.sentBackNote && check.status === "to_review" ? (
            <p className="m-0 rounded-md bg-warning-surface px-3.5 py-3 text-md text-warning-text">Sent back: {check.sentBackNote}</p>
          ) : null}
          {check.amountScale && check.amountScale !== "one" ? (
            <p className="m-0 text-sm text-muted">The statement counts its amounts in {check.amountScale}s; they are shown here in full.</p>
          ) : null}

          <section aria-label="Differences" className="flex flex-col gap-2">
            <h4 className="m-0 text-md font-extrabold">
              {differences.length === 0 ? "Everything matches" : `${differences.length} to explain${detail.unexplained ? `, ${detail.unexplained} without a note` : ""}`}
            </h4>
            {differences.map((item) => (
              <Difference key={item.key} item={item} rows={rows} busy={busy} canNote={may("note")} onNote={(text) => onNote(item.key, text)} />
            ))}
            {matches.length ? (
              <p className="m-0 text-sm text-muted">
                {matches.length} {matches.length === 1 ? "maturity matches" : "maturities match"}:{" "}
                {matches.map((item) => `${item.maturesOn ? formatDay(item.maturesOn) : ""} ${money(item.custodianFaceMinor, item.currency)}`).join("; ")}
              </p>
            ) : null}
          </section>

          <Rows rows={rows} busy={busy} canCorrect={may("correct")} onCorrect={onCorrect} />

          {error ? <ErrorNote message={error} /> : null}
          <Decisions check={check} unexplained={detail.unexplained} busy={busy} may={may} onSubmit={onSubmit} onSignOff={onSignOff} onSendBack={onSendBack} />
        </>
      )}
    </article>
  );
}

function Difference({ item, rows, busy, canNote, onNote }: { item: CheckItem; rows: StatementRow[]; busy: boolean; canNote: boolean; onNote: (note: string) => void }) {
  const id = useId();
  const [text, setText] = useState(item.note ?? "");
  const lines = rows.filter((row) => item.rowIds.includes(row.id)).map((row) => `line ${row.lineNo}`);
  const what =
    item.kind === "statement_date_differs"
      ? `The statement says it is for ${item.maturesOn ? formatDay(item.maturesOn) : "another day"}.`
      : item.kind === "statement_date_unread"
        ? "No date could be read off the statement."
        : item.kind === "row_unreadable"
          ? `${lines.join(", ")}: ${PROBLEM[item.problem ?? ""] ?? "could not be counted"}. Correct it below, or leave it out.`
          : null;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-md font-bold">
          {KIND[item.kind]}
          {item.maturesOn && item.currency ? ` · matures ${formatDay(item.maturesOn)}` : ""}
        </span>
        {item.currency ? (
          <span className={cn("text-md font-bold", item.differenceMinor !== 0 && "text-danger")}>
            {item.differenceMinor > 0 ? "+" : ""}
            {money(item.differenceMinor, item.currency)}
          </span>
        ) : null}
      </div>
      {what ? (
        <p className="m-0 text-sm">{what}</p>
      ) : (
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted">Finvest holds</dt>
          <dd className="m-0">
            {money(item.finvestFaceMinor, item.currency)}
            {item.productCodes.length ? ` (${item.productCodes.join(", ")})` : ""}
          </dd>
          <dt className="text-muted">Statement shows</dt>
          <dd className="m-0">
            {money(item.custodianFaceMinor, item.currency)}
            {lines.length ? ` (${lines.join(", ")})` : ""}
          </dd>
        </dl>
      )}
      {canNote ? (
        <form
          aria-label={`Note on ${KIND[item.kind].toLowerCase()}`}
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (text.trim()) onNote(text.trim());
          }}
        >
          <div className="flex min-w-0 flex-[1_1_240px] flex-col gap-1.5">
            <Label htmlFor={`${id}-note`}>Why it differs</Label>
            <Input id={`${id}-note`} value={text} maxLength={1000} onChange={(event) => setText(event.target.value)} />
          </div>
          <Button type="submit" variant="secondary" disabled={busy || !text.trim() || text.trim() === item.note}>
            Save note
          </Button>
        </form>
      ) : item.note ? (
        <p className="m-0 text-sm">
          <span className="font-bold">Note:</span> {item.note}
        </p>
      ) : (
        <p className="m-0 text-sm text-danger">No note yet.</p>
      )}
    </div>
  );
}

function Rows({ rows, busy, canCorrect, onCorrect }: { rows: StatementRow[]; busy: boolean; canCorrect: boolean; onCorrect: (rowId: string, correction: Correction) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  if (rows.length === 0) return <p className="m-0 text-md text-muted">No holdings were found on the statement.</p>;
  return (
    <section aria-label="Lines as read" className="flex flex-col gap-2">
      <h4 className="m-0 text-md font-extrabold">Every line as read, beside what was counted</h4>
      <p className="m-0 text-sm text-muted">Check each one against the original. A line the reader got wrong can be corrected with a reason; what it read is kept.</p>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-col gap-2 rounded-lg border border-border p-3.5">
            <div className="flex flex-wrap gap-4">
              <div className="flex min-w-0 flex-[1_1_260px] flex-col gap-0.5">
                <span className="text-xs font-bold uppercase text-muted">
                  Line {row.lineNo} · {row.location}
                </span>
                <span className="text-md font-bold">
                  {row.instrument}
                  {row.code ? ` · ${row.code}` : ""}
                </span>
                <code className="whitespace-pre-wrap break-words rounded bg-surface-sunken px-2 py-1 text-sm">{row.sourceText}</code>
                <span className="text-sm text-muted">
                  Read as {row.faceValueText || "no amount"} {row.currencyText ?? ""}, maturing {row.maturesOnText ?? "no date"}
                </span>
              </div>
              <div className="flex min-w-0 flex-[1_1_200px] flex-col gap-0.5">
                <span className="text-xs font-bold uppercase text-muted">Counted as</span>
                <Counted row={row} />
              </div>
            </div>
            {canCorrect ? (
              editing === row.id ? (
                <CorrectForm
                  row={row}
                  busy={busy}
                  onCancel={() => setEditing(null)}
                  onSave={(correction) => {
                    onCorrect(row.id, correction);
                    setEditing(null);
                  }}
                />
              ) : (
                <Button variant="secondary" className="self-start" onClick={() => setEditing(row.id)}>
                  Correct this line
                </Button>
              )
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function Counted({ row }: { row: StatementRow }) {
  if (row.leftOut) {
    return (
      <>
        <span className="text-md font-bold">Left out</span>
        <span className="text-sm text-muted">{row.correctionReason}</span>
      </>
    );
  }
  const face = row.correctedFaceMinor ?? row.faceMinor;
  const day = row.correctedMaturesOn ?? row.maturesOn;
  if (face === null || !day) {
    return <span className="text-md font-bold text-danger">{PROBLEM[row.problem ?? ""] ?? "Could not be counted"}</span>;
  }
  return (
    <>
      <span className="text-md font-bold">{formatExact({ minor: face, currency: row.currency ?? "UGX" })}</span>
      <span className="text-sm">Matures {formatDay(day)}</span>
      {row.correctedBy ? <span className="text-sm text-warning-text">Corrected: {row.correctionReason}</span> : null}
    </>
  );
}

function CorrectForm({ row, busy, onCancel, onSave }: { row: StatementRow; busy: boolean; onCancel: () => void; onSave: (correction: Correction) => void }) {
  const id = useId();
  const [amount, setAmount] = useState("");
  const [maturesOn, setMaturesOn] = useState("");
  const [leftOut, setLeftOut] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      aria-label={`Correct line ${row.lineNo}`}
      className="flex flex-col gap-3 rounded-lg bg-surface-sunken p-3.5"
      onSubmit={(event) => {
        event.preventDefault();
        const face = amount.trim() ? parseAmount(amount, row.currency ?? "UGX") : null;
        if (amount.trim() && !face) return setProblem("Enter the face value as whole shillings, such as 800,000,000.");
        if (!leftOut && !face && !maturesOn) return setProblem("Enter the right amount or maturity date, or leave the line out.");
        if (!reason.trim()) return setProblem("Say why. It is kept with the correction.");
        setProblem(null);
        onSave({ leftOut, faceMinor: face?.minor ?? null, maturesOn: maturesOn || null, reason: reason.trim() });
      }}
    >
      <label className="flex items-center gap-2 text-md">
        <input type="checkbox" checked={leftOut} onChange={(event) => setLeftOut(event.target.checked)} />
        Leave this line out (a total, cash or a line that is not a holding)
      </label>
      {leftOut ? null : (
        <div className="flex flex-wrap gap-3">
          <div className="flex min-w-0 flex-[1_1_180px] flex-col gap-1.5">
            <Label htmlFor={`${id}-amount`}>Face value (UGX)</Label>
            <Input id={`${id}-amount`} inputMode="numeric" value={amount} onChange={(event) => setAmount(event.target.value)} />
          </div>
          <div className="flex min-w-0 flex-[1_1_180px] flex-col gap-1.5">
            <Label htmlFor={`${id}-matures`}>Maturity date</Label>
            <Input id={`${id}-matures`} type="date" value={maturesOn} onChange={(event) => setMaturesOn(event.target.value)} />
          </div>
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-reason`}>Reason</Label>
        <Input id={`${id}-reason`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
      </div>
      {problem ? <ErrorNote message={problem} /> : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          Save correction
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Decisions({
  check,
  unexplained,
  busy,
  may,
  onSubmit,
  onSignOff,
  onSendBack,
}: {
  check: CheckSummary;
  unexplained: number;
  busy: boolean;
  may: (action: CheckSummary["allowed"][number]) => boolean;
  onSubmit: (note: string | null) => void;
  onSignOff: (note: string | null) => void;
  onSendBack: (reason: string) => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  if (check.status === "signed_off") {
    return (
      <p className="m-0 rounded-md bg-success-surface px-3.5 py-3 text-md text-success">
        Signed off{check.signedOffAt ? ` ${formatMoment(check.signedOffAt)}` : ""}.{check.signOffNote ? ` ${check.signOffNote}` : ""}
      </p>
    );
  }
  if (may("submit")) {
    return (
      <div className="flex flex-col gap-2 border-t border-divider pt-4">
        <Label htmlFor={`${id}-submit`}>Note for the person signing off (optional)</Label>
        <Input id={`${id}-submit`} value={text} maxLength={1000} onChange={(event) => setText(event.target.value)} />
        <p className="m-0 text-sm text-muted">
          {unexplained ? `Explain the ${unexplained === 1 ? "difference" : `${unexplained} differences`} without a note first.` : "Finvest compares again with the latest holdings before it is sent."}
        </p>
        <Button disabled={busy || unexplained > 0} className="h-12 self-start px-6" onClick={() => onSubmit(text.trim() || null)}>
          Send for sign-off
        </Button>
      </div>
    );
  }
  if (check.status === "waiting_sign_off") {
    if (!may("sign_off")) {
      return <p className="m-0 text-md text-muted">Sent for sign-off. A different person must sign it off or send it back.</p>;
    }
    return (
      <div className="flex flex-col gap-2 border-t border-divider pt-4">
        {check.submitNote ? (
          <p className="m-0 text-md">
            <span className="font-bold">From the reviewer:</span> {check.submitNote}
          </p>
        ) : null}
        <Label htmlFor={`${id}-decide`}>Your note (needed to send it back)</Label>
        <Input id={`${id}-decide`} value={text} maxLength={1000} onChange={(event) => setText(event.target.value)} />
        {problem ? <ErrorNote message={problem} /> : null}
        <p className="m-0 text-md leading-normal">Signing off records that the custodian's statement agrees with Finvest's records, with every difference explained.</p>
        <div className="flex flex-wrap gap-2.5">
          <Button disabled={busy} className="h-12 flex-[1_1_180px]" onClick={() => onSignOff(text.trim() || null)}>
            Sign off as second person
          </Button>
          <Button
            variant="secondary"
            disabled={busy}
            className="h-12 flex-[1_1_180px]"
            onClick={() => {
              if (!text.trim()) return setProblem("Say what needs another look.");
              setProblem(null);
              onSendBack(text.trim());
            }}
          >
            Send back
          </Button>
        </div>
      </div>
    );
  }
  return null;
}

