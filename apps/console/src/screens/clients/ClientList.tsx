import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { clientsErrorMessage, type ClientRow, type ClientsApi, type ClientStatus } from "./api.ts";
import { formatAccountNumber, formatTime, STATUS_LABELS } from "./labels.ts";
import { STATUS_TONE } from "./Parts.tsx";

const FILTERS: readonly { value: ClientStatus | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "active", label: "Active" },
  { value: "locked", label: "Locked" },
  { value: "unclaimed", label: "Not signed up yet" },
  { value: "closed", label: "Closed" },
];

type Load =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; rows: ClientRow[]; nextAfter: string | null; more: boolean; loadingMore: boolean; moreFailed: string | null };

interface ClientListProps {
  api: ClientsApi;
  /** The search as sent, or "" for every client. */
  query: string;
  onOpen: (clientId: string) => void;
}

/** Every client newest first, or those a name or email search finds, with a status filter. Give it a `key` of the query, so a new search starts afresh. */
export function ClientList({ api, query, onOpen }: ClientListProps) {
  const [status, setStatus] = useState<ClientStatus | null>(null);
  const [load, setLoad] = useState<Load>({ state: "loading" });

  const fetchFirst = useCallback(
    (): Promise<Load> =>
      api.list(query, status, null).then(
        (found) => ({ state: "ready", rows: found.clients, nextAfter: found.nextAfter, more: found.more, loadingMore: false, moreFailed: null }),
        (error: unknown) => ({ state: "failed", message: clientsErrorMessage(error) }),
      ),
    [api, query, status],
  );

  useEffect(() => {
    let current = true;
    void fetchFirst().then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetchFirst]);

  async function showMore() {
    if (load.state !== "ready" || !load.nextAfter) return;
    const shown = load;
    setLoad({ ...shown, loadingMore: true, moreFailed: null });
    try {
      const found = await api.list(query, status, shown.nextAfter);
      setLoad({ ...shown, rows: [...shown.rows, ...found.clients], nextAfter: found.nextAfter, loadingMore: false });
    } catch (error) {
      setLoad({ ...shown, loadingMore: false, moreFailed: clientsErrorMessage(error) });
    }
  }

  const title = query ? `Clients matching “${query}”` : "All clients";
  return (
    <section aria-labelledby="client-list" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="client-list" className="m-0 text-xl font-extrabold">
          {title}
        </h2>
        <div role="group" aria-label="Show clients who are" className="flex flex-wrap gap-1.5">
          {FILTERS.map((filter) => (
            <button
              key={filter.label}
              type="button"
              aria-pressed={status === filter.value}
              onClick={() => {
                if (filter.value === status) return;
                setLoad({ state: "loading" });
                setStatus(filter.value);
              }}
              className={cn(
                "min-h-9 cursor-pointer rounded-pill border px-3 text-sm font-bold",
                status === filter.value ? "border-black bg-black text-white" : "border-border-strong bg-surface text-ink hover:bg-surface-sunken",
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading clients
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-2">
          <p className="m-0 text-md font-bold">Clients could not be loaded</p>
          <p className="m-0 text-md text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchFirst().then(setLoad)}>
            Try again
          </Button>
        </div>
      ) : load.rows.length === 0 ? (
        <p role="status" className="m-0 text-md text-muted">
          {query ? `No client's verified name or email matches “${query}”${status ? " with that status" : ""}.` : status ? "No client has that status." : "No clients yet."}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`${title}, table`}>
            <table className="w-full border-collapse text-left text-md">
              <caption className="sr-only">{title}, newest first</caption>
              <thead>
                <tr className="text-xs font-bold tracking-[0.6px] text-muted uppercase">
                  {["Name", "Account number", "Mobile", "Email", "Status", "Joined"].map((heading) => (
                    <th key={heading} scope="col" className="px-2.5 py-2 whitespace-nowrap">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {load.rows.map((row) => (
                  <tr key={row.id} className="border-t border-divider hover:bg-paper">
                    <th scope="row" className="px-2.5 py-2.5 font-bold">
                      <button
                        type="button"
                        onClick={() => onOpen(row.id)}
                        className="inline-flex min-h-6 cursor-pointer items-center text-left underline-offset-2 hover:underline"
                      >
                        {row.verifiedName ?? <span className="font-semibold text-muted">Name not verified yet</span>}
                      </button>
                    </th>
                    <td className="px-2.5 py-2.5 whitespace-nowrap tabular-nums">{row.accountNumber ? formatAccountNumber(row.accountNumber) : "None yet"}</td>
                    <td className="px-2.5 py-2.5 whitespace-nowrap tabular-nums">{row.phone}</td>
                    <td className="px-2.5 py-2.5 break-all">{row.email ?? "None given"}</td>
                    <td className="px-2.5 py-2.5">
                      <span className={cn("rounded-pill px-2.5 py-0.5 text-xs font-bold whitespace-nowrap", STATUS_TONE[row.status])}>{STATUS_LABELS[row.status]}</span>
                    </td>
                    <td className="px-2.5 py-2.5 whitespace-nowrap text-muted">{formatTime(row.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {load.more ? <p className="m-0 text-sm text-muted">Showing the newest {load.rows.length}. Add a word to narrow the search.</p> : null}
          {load.moreFailed ? (
            <p role="alert" className="m-0 text-sm font-bold text-danger">
              {load.moreFailed}
            </p>
          ) : null}
          {load.nextAfter ? (
            <Button variant="secondary" className="self-start" disabled={load.loadingMore} onClick={() => void showMore()}>
              {load.loadingMore ? "Loading" : "Show more"}
            </Button>
          ) : null}
        </>
      )}
    </section>
  );
}
