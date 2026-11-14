import { useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatExact } from "../../products/format.ts";
import { AT_MATURITY_WORDS, bookingsErrorMessage, type BookableProduct, type BookingsError, type NewBooking } from "./api.ts";
import { readSheet, rowProblemText, sheetErrorText, TEMPLATE, type SheetRow } from "./sheet.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const CELL = "border-b border-divider px-3 py-2 text-left align-top";
const REASON_LIMIT = 500;

type Outcome = { state: "sent" } | { state: "refused"; message: string };
interface Row extends SheetRow {
  client: ClientCard | null;
}

interface UploadFormProps {
  directory: Directory;
  products: readonly BookableProduct[];
  newKey: () => string;
  /** Books one row with its own key. Null when it was booked, otherwise why not. */
  onBook: (input: NewBooking, key: string) => Promise<BookingsError | null>;
  onDone: () => Promise<void>;
  onClose: () => void;
}

function textOf(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(reader.error ?? new Error("unreadable"));
    reader.readAsText(file);
  });
}

/** Book many investments from one CSV file. Each row is a booking a second person approves. */
export function UploadForm({ directory, products, newKey, onBook, onDone, onClose }: UploadFormProps) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const [sending, setSending] = useState(false);
  const [outcomes, setOutcomes] = useState<Record<number, Outcome>>({});
  // One key per row of one file, so sending again after a lost answer books each row once.
  const keys = useRef(new Map<string, string>());

  async function choose(file: File | undefined) {
    setRows(null);
    setOutcomes({});
    setFileError(null);
    keys.current.clear();
    if (!file) return;
    setReading(true);
    let text: string;
    try {
      text = await textOf(file);
    } catch {
      setFileError("We could not read the file. Save it as CSV and choose it again.");
      setReading(false);
      return;
    }
    const sheet = readSheet(text, products);
    if (!sheet.ok) {
      setFileError(sheetErrorText(sheet));
      setReading(false);
      return;
    }
    const numbers = [...new Set(sheet.rows.map((row) => row.accountNumber).filter(Boolean))];
    const found = new Map<string, ClientCard>();
    await Promise.all(
      numbers.map(async (number) => {
        const cards = await directory.findClients(number).catch(() => []);
        const card = cards.find((c) => c.accountNumber === number && c.verified && c.status === "active");
        if (card) found.set(number, card);
      }),
    );
    setRows(sheet.rows.map((row) => ({ ...row, client: found.get(row.accountNumber) ?? null })));
    setReading(false);
  }

  const ready = (rows ?? []).filter((row) => row.client && row.problems.length === 0 && outcomes[row.line]?.state !== "sent");
  const reasonProblem = !reason.trim() ? "Fill this in." : reason.length > REASON_LIMIT ? "Keep it to 500 characters." : null;
  const sent = Object.values(outcomes).filter((o) => o.state === "sent").length;
  const refused = Object.values(outcomes).filter((o) => o.state === "refused").length;

  async function send() {
    setTried(true);
    if (reasonProblem || ready.length === 0) return;
    setSending(true);
    for (const row of ready) {
      if (!row.client || !row.product || !row.amount) continue;
      const input: NewBooking = {
        clientId: row.client.clientId,
        productCode: row.product.code,
        amount: row.amount,
        atMaturity: row.atMaturity,
        reason: `${reason.trim()} (row ${row.line} of the uploaded sheet)`,
      };
      const id = `${row.line}:${JSON.stringify(input)}`;
      const key = keys.current.get(id) ?? newKey();
      keys.current.set(id, key);
      const error = await onBook(input, key);
      setOutcomes((known) => ({ ...known, [row.line]: error ? { state: "refused", message: bookingsErrorMessage(error) } : { state: "sent" } }));
    }
    await onDone();
    setSending(false);
  }

  return (
    <form
      aria-label="Book investments from a spreadsheet"
      className="flex flex-col gap-3 border-t border-divider pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <p className="m-0 text-sm leading-normal text-muted">
        A CSV file with a header row and one investment per row: <code>account_number</code>, <code>product_code</code>, <code>amount</code> in whole
        units, and <code>at_maturity</code> (<code>roll_over</code>, <code>pay_out</code> or <code>pay_interest</code>, or blank to roll over). Each
        row becomes a booking someone else approves, and each client pays for theirs in the app.{" "}
        <a className="font-bold text-ink underline" download="finvest-bookings.csv" href={`data:text/csv;charset=utf-8,${encodeURIComponent(TEMPLATE)}`}>
          Download the template
        </a>
      </p>
      <label className="flex max-w-[420px] flex-col gap-1.5 text-sm font-bold">
        Spreadsheet
        <input type="file" accept=".csv,text/csv" disabled={sending} onChange={(event) => void choose(event.target.files?.[0])} className="text-sm font-medium" />
      </label>
      {reading ? (
        <p role="status" className="m-0 text-sm text-muted">
          Reading the file and finding the clients
        </p>
      ) : null}
      {fileError ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {fileError}
        </p>
      ) : null}
      {rows ? (
        <ul aria-label="Rows to book, one per card" className="m-0 flex list-none flex-col gap-2 p-0 md:hidden">
          {rows.map((row) => (
            <li key={row.line} className="flex flex-col gap-1 rounded-lg border border-divider p-3 text-sm">
              <span className="text-xs font-bold text-muted">Row {row.line}</span>
              <span>
                {row.client ? <strong>{row.client.name}</strong> : null} <span className="text-muted">{row.accountNumber || "No account number"}</span>
              </span>
              <span>{investmentText(row)}</span>
              {row.atMaturity ? <span className="text-muted">{AT_MATURITY_WORDS[row.atMaturity]}</span> : null}
              <span className="font-semibold">
                <Check row={row} outcome={outcomes[row.line]} />
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {rows ? (
        <div className="hidden overflow-x-auto md:block">
          <table aria-label="Rows to book" className="w-full min-w-[640px] border-collapse text-sm">
            <thead>
              <tr className="text-xs font-bold text-muted">
                <th className={CELL}>Row</th>
                <th className={CELL}>Client</th>
                <th className={CELL}>Investment</th>
                <th className={CELL}>Check</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                return (
                  <tr key={row.line}>
                    <td className={CELL}>{row.line}</td>
                    <td className={CELL}>
                      {row.client ? <strong>{row.client.name}</strong> : null}
                      <div className="text-muted">{row.accountNumber || "No account number"}</div>
                    </td>
                    <td className={CELL}>
                      <div>{investmentText(row)}</div>
                      {row.atMaturity ? <div className="text-muted">{AT_MATURITY_WORDS[row.atMaturity]}</div> : null}
                    </td>
                    <td className={cn(CELL, "font-semibold")}>
                      <Check row={row} outcome={outcomes[row.line]} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {rows ? (
        <label className="flex flex-col gap-1.5 text-sm font-bold">
          <span className="flex justify-between gap-2">
            How did the clients ask?
            <span className={cn("text-xs font-semibold", reason.length > REASON_LIMIT ? "text-danger" : "text-muted")}>
              {reason.length} of {REASON_LIMIT}
            </span>
          </span>
          <span className="text-xs font-medium text-muted">Saved on every booking for the person who approves it and the audit trail.</span>
          <textarea rows={2} value={reason} aria-invalid={tried && reasonProblem !== null} onChange={(event) => setReason(event.target.value)} className={INPUT} />
          {tried && reasonProblem ? <span className="text-xs font-bold text-danger">{reasonProblem}</span> : null}
        </label>
      ) : null}
      {sent + refused > 0 && !sending ? (
        <p role="status" className="m-0 text-sm font-semibold">
          {sent} sent for approval{refused > 0 ? `, ${refused} refused by Finvest` : ""}.
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {rows && ready.length > 0 ? (
          <Button type="submit" disabled={sending || ready.length === 0}>
            {sending ? "Sending" : `Send ${ready.length} for approval`}
          </Button>
        ) : null}
        <Button variant="secondary" disabled={sending} onClick={onClose}>
          {sent > 0 ? "Done" : "Cancel"}
        </Button>
      </div>
    </form>
  );
}

function investmentText(row: Row): string {
  return `${row.amount ? formatExact(row.amount) : row.amountText || "No amount"} in ${row.product?.name ?? row.productCode}`;
}

function Check({ row, outcome }: { row: Row; outcome: Outcome | undefined }) {
  const problems = [...(row.accountNumber && !row.client ? ["No approved, active client has this account number."] : []), ...row.problems.map(rowProblemText)];
  if (outcome?.state === "sent") return <span className="text-success">Sent for approval</span>;
  if (outcome?.state === "refused") return <span className="text-danger">{outcome.message}</span>;
  if (problems.length > 0) return <span className="text-danger">{problems.join(" ")}</span>;
  return <span className="text-success">Ready</span>;
}
