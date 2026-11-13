import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import type { Currency } from "@/screens/dealing-desk/api.ts";
import { parseAmount } from "@/screens/dealing-desk/format.ts";
import { MAX_DETAILS, MAX_SUMMARY, type Category, type LoggedVia, type NewComplaint } from "./api.ts";
import { CATEGORIES, CATEGORY_LABELS, LOGGED_VIA, LOGGED_VIA_LABELS } from "./labels.ts";

interface LogFormProps {
  directory: Directory;
  busy: boolean;
  error: string | null;
  onSubmit: (complaint: NewComplaint) => void;
  onCancel: () => void;
}

const FIELD = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink";

/** Log a complaint that came by chat, phone, email, letter or in person. A client logs their own in the app. */
export function LogForm({ directory, busy, error, onSubmit, onCancel }: LogFormProps) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ClientCard[]>([]);
  const [client, setClient] = useState<ClientCard | null>(null);
  const [category, setCategory] = useState<Category | "">("");
  const [via, setVia] = useState<LoggedVia | "">("");
  const [summary, setSummary] = useState("");
  const [details, setDetails] = useState("");
  const [amountText, setAmountText] = useState("");
  const [currency, setCurrency] = useState<Currency>("UGX");
  const [tried, setTried] = useState(false);

  useEffect(() => {
    let current = true;
    void directory.findClients(client ? "" : query).then(
      (clients) => current && setFound(clients),
      () => current && setFound([]),
    );
    return () => {
      current = false;
    };
  }, [directory, query, client]);

  const amount = amountText.trim() === "" ? null : parseAmount(amountText, currency);
  const amountBad = amountText.trim() !== "" && amount === null;
  const problems = [
    client ? null : "Choose the client",
    category ? null : "Choose what it is about",
    via ? null : "Say how it came in",
    summary.trim() ? null : "Write a one line summary",
    details.trim() ? null : "Write what the client said",
    amountBad ? "Write the amount in whole units, like 150,000" : null,
  ].filter((problem): problem is string => problem !== null);

  return (
    <form
      aria-label="Log a complaint"
      className="flex flex-col gap-3 rounded-[20px] border border-border bg-surface p-5"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (problems.length > 0 || !client || !category || !via) return;
        onSubmit({ clientId: client.clientId, category, receivedVia: via, summary, details, amount });
      }}
    >
      <h2 className="m-0 text-[18px] font-extrabold">Log a complaint</h2>
      <p className="m-0 text-sm leading-normal text-muted">
        For one that came by chat, phone, email, letter or in person. The client gets the reference and the date we will reply by.
      </p>

      {client ? (
        <div className="flex items-center justify-between gap-2 rounded-lg bg-paper px-3.5 py-3 text-sm">
          <span>
            <strong>{client.name}</strong>
            <span className="text-muted">{client.accountNumber ? `, account ${client.accountNumber}` : ", no account number yet"}</span>
          </span>
          <Button variant="secondary" className="h-9 px-3" onClick={() => setClient(null)}>
            Change
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            Client
            <Input
              value={query}
              placeholder="Name or account number"
              onChange={(event) => setQuery(event.target.value)}
              className="h-11 text-md"
            />
          </label>
          {found.length > 0 ? (
            <ul aria-label="Matching clients" className="m-0 flex list-none flex-col gap-1 p-0">
              {found.map((card) => (
                <li key={card.clientId}>
                  <button
                    type="button"
                    onClick={() => setClient(card)}
                    className="w-full cursor-pointer rounded-md px-3 py-2 text-left text-sm hover:bg-paper"
                  >
                    <strong>{card.name}</strong>
                    <span className="text-muted">{card.accountNumber ? `, ${card.accountNumber}` : ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : query.trim().length >= 2 ? (
            <p className="m-0 text-xs text-muted">No client matches that.</p>
          ) : null}
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        <label className="flex min-w-[160px] flex-1 flex-col gap-1.5 text-sm font-bold">
          About
          <select value={category} onChange={(event) => setCategory(event.target.value as Category)} className={FIELD}>
            <option value="" disabled>
              Choose
            </option>
            {CATEGORIES.map((option) => (
              <option key={option} value={option}>
                {CATEGORY_LABELS[option]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-[160px] flex-1 flex-col gap-1.5 text-sm font-bold">
          How it came in
          <select value={via} onChange={(event) => setVia(event.target.value as LoggedVia)} className={FIELD}>
            <option value="" disabled>
              Choose
            </option>
            {LOGGED_VIA.map((option) => (
              <option key={option} value={option}>
                {LOGGED_VIA_LABELS[option]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        Summary
        <Input value={summary} maxLength={MAX_SUMMARY} onChange={(event) => setSummary(event.target.value)} className="h-11 text-md" />
      </label>
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        What the client said
        <span className="text-xs font-medium text-muted">In their words. This cannot be changed once logged.</span>
        <textarea value={details} rows={4} maxLength={MAX_DETAILS} onChange={(event) => setDetails(event.target.value)} className={FIELD} />
      </label>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-[180px] flex-[2_1_180px] flex-col gap-1.5 text-sm font-bold">
          Amount in question, if any
          <Input
            value={amountText}
            inputMode="numeric"
            aria-invalid={tried && amountBad}
            onChange={(event) => setAmountText(event.target.value)}
            className="h-11 text-md"
          />
        </label>
        <label className="flex min-w-[100px] flex-1 flex-col gap-1.5 text-sm font-bold">
          Currency
          <select value={currency} onChange={(event) => setCurrency(event.target.value as Currency)} className={FIELD}>
            <option value="UGX">UGX</option>
            <option value="USD">USD</option>
          </select>
        </label>
      </div>
      <p className="m-0 text-xs text-muted">The amount is a note for staff. It moves no money.</p>
      {tried && problems.length > 0 ? (
        <ul className="m-0 flex flex-col gap-0.5 pl-4 text-xs font-bold text-danger">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Logging" : "Log complaint"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
