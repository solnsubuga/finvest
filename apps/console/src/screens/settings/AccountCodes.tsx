import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import {
  AccountCodesError,
  accountCodesErrorMessage,
  checkAccount,
  codeProblemText,
  type AccountCodesApi,
  type AccountingSystem,
  type CodeMaps,
  type CodeProblem,
  type LedgerCode,
  type MapDecision,
  type MapVersion,
  type NewMap,
  type SystemMap,
} from "./accountCodes.ts";
import { stamp } from "./labels.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3 py-2 text-md font-medium text-ink aria-invalid:border-danger";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; maps: CodeMaps };

interface AccountCodesSectionProps {
  api: AccountCodesApi;
  /** `account_codes.change`: propose and decide. The API checks it again. */
  canChange: boolean;
}

function statusOf(item: SystemMap): { label: string; className: string } {
  if (item.proposal) return { label: "Change waiting", className: "bg-warning-surface text-warning-text" };
  if (item.active) return { label: "In use", className: "bg-success-surface text-success" };
  return { label: "Not set up", className: "bg-surface-sunken text-muted" };
}

function person(version: MapVersion, who: "proposed" | "decided"): string {
  if (who === "proposed") return version.yours ? "you" : (version.proposedByName ?? "a former member of staff");
  return version.decidedByName ?? "a former member of staff";
}

/**
 * Which account in each accounting system every Finvest ledger code is posted
 * to. Exports for a system use its approved map, and refuse a day that moved
 * a code the map does not name. One person proposes a whole map and a
 * different person approves it.
 */
export function AccountCodesSection({ api, canChange }: AccountCodesSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [selected, setSelected] = useState<AccountingSystem>("quickbooks");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ message: string; problems: readonly CodeProblem[] } | null>(null);

  const fetchMaps = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (maps) => ({ state: "ready", maps }),
        (error: unknown) => ({ state: "failed", message: accountCodesErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchMaps().then((next) => {
      if (!current) return;
      setLoad(next);
      // A change waiting for a decision comes first, as it is the work to do.
      if (next.state === "ready") {
        const waiting = next.maps.systems.find((s) => s.proposal);
        if (waiting) setSelected(waiting.system);
      }
    });
    return () => {
      current = false;
    };
  }, [fetchMaps]);

  async function run(command: () => Promise<CodeMaps>): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    try {
      const maps = await command();
      setLoad({ state: "ready", maps });
      return true;
    } catch (error) {
      setFailed({ message: accountCodesErrorMessage(error), problems: error instanceof AccountCodesError ? error.problems : [] });
      if (error instanceof AccountCodesError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchMaps();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  const heading = (
    <div className="flex max-w-[760px] flex-col gap-1">
      <h2 id="account-codes" className="m-0 text-xl font-extrabold">
        Accounting system codes
      </h2>
      <p className="m-0 text-md leading-normal text-muted">
        The account in each accounting system that every Finvest account is posted to, for the daily journal on the Finance screen. One person
        proposes the codes and someone else approves them. A day that moved an account with no code here cannot be downloaded for that system.
      </p>
    </div>
  );

  if (load.state === "loading") {
    return (
      <section aria-labelledby="account-codes" className="flex flex-col gap-4">
        {heading}
        <p role="status" className="m-0 text-md text-muted">
          Loading accounting system codes
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section aria-labelledby="account-codes" className="flex flex-col gap-4">
        {heading}
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">Accounting system codes could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchMaps().then(setLoad);
            }}
          >
            Try again
          </Button>
        </section>
      </section>
    );
  }

  const { maps } = load;
  const current = maps.systems.find((s) => s.system === selected) ?? maps.systems[0];

  return (
    <section aria-labelledby="account-codes" className="flex flex-col gap-4">
      {heading}
      <div role="tablist" aria-label="Accounting systems" className="flex flex-wrap gap-2">
        {maps.systems.map((item) => {
          const status = statusOf(item);
          const on = item.system === current?.system;
          return (
            <button
              key={item.system}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => {
                setSelected(item.system);
                setEditing(false);
                setFailed(null);
              }}
              className={cn(
                "flex items-center gap-2 rounded-pill border px-4 py-2 text-md font-bold",
                on ? "border-ink bg-ink text-surface" : "border-border-strong bg-surface text-ink",
              )}
            >
              {item.name}
              <span className={cn("rounded-pill px-2 py-0.5 text-xs font-bold", status.className)}>{status.label}</span>
            </button>
          );
        })}
      </div>
      {current ? (
        <SystemPanel
          key={`${current.system}:${current.active?.id ?? ""}:${current.proposal?.id ?? ""}`}
          item={current}
          codes={maps.codes}
          canChange={canChange}
          editing={editing}
          busy={busy}
          error={failed?.message ?? null}
          refused={failed?.problems ?? []}
          onEdit={() => {
            setEditing(true);
            setFailed(null);
          }}
          onCancel={() => {
            setEditing(false);
            setFailed(null);
          }}
          onPropose={(map) =>
            run(() => api.propose(current.system, map)).then((done) => {
              if (done) setEditing(false);
              return done;
            })
          }
          onDecide={(mapId, decision, reason) => run(() => api.decide(mapId, decision, reason))}
        />
      ) : null}
    </section>
  );
}

interface SystemPanelProps {
  item: SystemMap;
  codes: readonly LedgerCode[];
  canChange: boolean;
  editing: boolean;
  busy: boolean;
  error: string | null;
  refused: readonly CodeProblem[];
  onEdit: () => void;
  onCancel: () => void;
  onPropose: (map: NewMap) => Promise<boolean>;
  onDecide: (mapId: string, decision: MapDecision, reason: string) => Promise<boolean>;
}

function SystemPanel({ item, codes, canChange, editing, busy, error, refused, onEdit, onCancel, onPropose, onDecide }: SystemPanelProps) {
  const { active, proposal } = item;
  const mapped = active ? codes.filter((c) => active.accounts[c.code]).length : 0;
  return (
    <div role="tabpanel" aria-label={item.name} className="flex max-w-[960px] flex-col gap-4 rounded-[20px] border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="m-0 text-[17px] font-extrabold">{item.name}</h3>
          <p className="m-0 text-sm leading-normal text-muted">
            {active && active.decidedAt
              ? `In use since ${stamp(active.decidedAt)}: proposed by ${person(active, "proposed")}, approved by ${person(active, "decided")}. ${mapped} of ${codes.length} Finvest accounts have a code.`
              : `No codes approved yet, so the journal cannot be downloaded for ${item.name}.`}
            {active?.taxRate ? ` Tax rate on every line: ${active.taxRate}.` : ""}
          </p>
        </div>
        {canChange && !editing && !proposal ? (
          <Button className="px-[18px]" onClick={onEdit}>
            {active ? "Change codes" : "Set up codes"}
          </Button>
        ) : null}
      </div>

      {editing && !proposal ? (
        <MapForm item={item} codes={codes} busy={busy} error={error} refused={refused} onCancel={onCancel} onSubmit={onPropose} />
      ) : proposal ? (
        <>
          <ProposalView item={item} codes={codes} proposal={proposal} />
          <Decision proposal={proposal} canChange={canChange} busy={busy} error={error} onDecide={onDecide} />
        </>
      ) : active ? (
        <CodesTable item={item} codes={codes} accounts={active.accounts} />
      ) : (
        <p className="m-0 rounded-lg bg-surface-sunken p-4 text-md leading-normal text-muted">
          {canChange
            ? `Ask the accountant for the ${item.accountLabel.toLowerCase()} of each account in ${item.name}, then set them up here for a second person to approve.`
            : `Someone who can change accounting system codes sets them up here, and a second person approves them.`}
        </p>
      )}

      {item.history.length > 0 ? (
        <div className="flex flex-col gap-2 border-t border-divider pt-3">
          <h4 className="m-0 text-sm font-extrabold">Earlier changes</h4>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-muted">
            {item.history.map((version) => (
              <li key={version.id}>
                {version.status === "approved" ? "Approved" : version.status === "rejected" ? "Rejected" : "Withdrawn"}
                {version.decidedAt ? ` ${stamp(version.decidedAt)}` : ""}: proposed by {person(version, "proposed")}
                {version.status === "withdrawn" ? "" : `, decided by ${person(version, "decided")}`}. {version.reason}
                {version.decisionReason && version.status !== "withdrawn" ? ` (${version.decisionReason})` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function CodesTable({ item, codes, accounts, before }: { item: SystemMap; codes: readonly LedgerCode[]; accounts: Readonly<Record<string, string>>; before?: Readonly<Record<string, string>> }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border" tabIndex={0} role="region" aria-label={`${item.name} codes`}>
      <table className="w-full table-fixed border-collapse text-md break-words">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th scope="col" className="w-1/2 px-3 py-3 font-bold">
              Finvest account
            </th>
            <th scope="col" className="px-3 py-3 font-bold">
              {item.name} {item.accountLabel.toLowerCase()}
            </th>
            {before ? (
              <th scope="col" className="px-3 py-3 font-bold">
                In use now
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {codes.map((code) => {
            const value = accounts[code.code];
            const was = before?.[code.code];
            const changed = before !== undefined && (value ?? "") !== (was ?? "");
            return (
              <tr key={code.code} className={cn("border-t border-border-subtle", changed && "bg-warning-surface")}>
                <th scope="row" className="px-3 py-2.5 text-left font-bold">
                  {code.name}
                  <span className="block text-xs font-medium text-muted">{code.code}</span>
                </th>
                <td className={cn("px-3 py-2.5", value ? "font-semibold" : "text-muted")}>{value ?? "No code"}</td>
                {before ? <td className={cn("px-3 py-2.5", was ? "" : "text-muted")}>{was ?? "No code"}</td> : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProposalView({ item, codes, proposal }: { item: SystemMap; codes: readonly LedgerCode[]; proposal: MapVersion }) {
  const changed = codes.filter((c) => (proposal.accounts[c.code] ?? "") !== (item.active?.accounts[c.code] ?? "")).length;
  const rateChanged = item.needsTaxRate && proposal.taxRate !== (item.active?.taxRate ?? null);
  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-lg bg-warning-surface p-3 text-sm leading-normal text-warning-text">
        <p className="m-0 font-bold">
          Proposed by {person(proposal, "proposed")}, {stamp(proposal.proposedAt)}. {changed === 1 ? "1 account changes" : `${changed} accounts change`}
          {rateChanged ? `, and the tax rate becomes ${proposal.taxRate}` : ""}.
        </p>
        <p className="m-0">{proposal.reason}</p>
      </div>
      <p className="m-0 text-sm text-muted">Exports keep using the codes in use now until this is approved. Changed rows are marked.</p>
      <CodesTable item={item} codes={codes} accounts={proposal.accounts} before={item.active?.accounts ?? {}} />
    </div>
  );
}

function Decision({
  proposal,
  canChange,
  busy,
  error,
  onDecide,
}: {
  proposal: MapVersion;
  canChange: boolean;
  busy: boolean;
  error: string | null;
  onDecide: (mapId: string, decision: MapDecision, reason: string) => Promise<boolean>;
}) {
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  if (!canChange) return <p className="m-0 text-sm text-muted">Someone who can change accounting system codes decides it.</p>;
  const missing = tried && note.trim().length === 0;
  const decide = (decision: MapDecision) => {
    setTried(true);
    if (note.trim().length === 0) return;
    void onDecide(proposal.id, decision, note);
  };
  return (
    <div className="flex flex-col gap-2 border-t border-divider pt-3">
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        {proposal.yours ? "Why withdraw it?" : "Note for the audit trail"}
        <span className="text-xs font-medium text-muted">
          {proposal.yours ? "Someone else approves your change. You can only withdraw it." : "Say what you checked, such as the accountant's chart of accounts."}
        </span>
        <textarea value={note} rows={2} aria-invalid={missing} onChange={(event) => setNote(event.target.value)} className={INPUT} />
      </label>
      {missing ? <p className="m-0 text-xs font-bold text-danger">Write a note first. It goes on the audit trail.</p> : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {proposal.yours ? (
          <Button variant="secondary" disabled={busy} onClick={() => decide("withdrawn")}>
            {busy ? "Saving" : "Withdraw it"}
          </Button>
        ) : (
          <>
            <Button disabled={busy} onClick={() => decide("approved")}>
              {busy ? "Saving" : "Approve the codes"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => decide("rejected")}>
              Reject
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function MapForm({
  item,
  codes,
  busy,
  error,
  refused,
  onCancel,
  onSubmit,
}: {
  item: SystemMap;
  codes: readonly LedgerCode[];
  busy: boolean;
  error: string | null;
  refused: readonly CodeProblem[];
  onCancel: () => void;
  onSubmit: (map: NewMap) => Promise<boolean>;
}) {
  const [accounts, setAccounts] = useState<Record<string, string>>(() => ({ ...(item.active?.accounts ?? {}) }));
  const [taxRate, setTaxRate] = useState(item.active?.taxRate ?? item.defaultTaxRate ?? "");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const local = codes.flatMap((c) => {
    const problem = checkAccount(item.system, accounts[c.code] ?? "");
    return problem ? [{ on: c.code, code: problem }] : [];
  });
  const problems = [...local, ...(tried ? refused : [])];
  const problemFor = (on: string) => problems.find((p) => p.on === on)?.code ?? null;
  const missingReason = tried && reason.trim().length === 0;
  const missingRate = tried && item.needsTaxRate && taxRate.trim().length === 0;
  const filled = codes.filter((c) => (accounts[c.code] ?? "").trim()).length;

  return (
    <form
      aria-label={`${item.name} codes`}
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (local.length > 0 || reason.trim().length === 0 || (item.needsTaxRate && taxRate.trim().length === 0)) return;
        void onSubmit({ accounts, taxRate: item.needsTaxRate ? taxRate : null, reason });
      }}
    >
      <p className="m-0 text-sm leading-normal text-muted">
        Write each account&rsquo;s {item.accountLabel.toLowerCase()} as {item.name} shows it. Leave one blank to leave it without a code. {filled} of{" "}
        {codes.length} have a code.
      </p>
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full table-fixed border-collapse text-md break-words">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col" className="w-1/2 px-3 py-3 font-bold">
                Finvest account
              </th>
              <th scope="col" className="px-3 py-3 font-bold">
                {item.name} {item.accountLabel.toLowerCase()}
              </th>
            </tr>
          </thead>
          <tbody>
            {codes.map((code) => {
              const problem = problemFor(code.code);
              return (
                <tr key={code.code} className="border-t border-border-subtle align-top">
                  <th scope="row" className="px-3 py-2.5 text-left font-bold">
                    {code.name}
                    <span className="block text-xs font-medium text-muted">{code.code}</span>
                  </th>
                  <td className="px-3 py-2">
                    <input
                      aria-label={`${item.accountLabel} for ${code.name}`}
                      value={accounts[code.code] ?? ""}
                      aria-invalid={problem !== null}
                      inputMode={item.system === "sage" ? "numeric" : undefined}
                      onChange={(event) => setAccounts({ ...accounts, [code.code]: event.target.value })}
                      className={INPUT}
                    />
                    {problem ? <span className="mt-1 block text-xs font-bold text-danger">{codeProblemText(problem)}</span> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {item.needsTaxRate ? (
        <label className="flex max-w-[420px] flex-col gap-1.5 text-sm font-bold">
          Tax rate on every line
          <span className="text-xs font-medium text-muted">
            {item.name} asks for one. A movement between Finvest&rsquo;s accounts carries no tax, so use the name of your no-tax rate in {item.name}.
          </span>
          <input value={taxRate} aria-invalid={missingRate || problemFor("tax_rate") !== null} onChange={(event) => setTaxRate(event.target.value)} className={INPUT} />
          {missingRate ? <span className="text-xs font-bold text-danger">This is needed.</span> : null}
        </label>
      ) : null}
      <label className="flex max-w-[640px] flex-col gap-1.5 text-sm font-bold">
        Why change them?
        <span className="text-xs font-medium text-muted">Staff only, for the person who approves it and the audit trail.</span>
        <textarea value={reason} rows={2} aria-invalid={missingReason} onChange={(event) => setReason(event.target.value)} className={INPUT} />
        {missingReason ? <span className="text-xs font-bold text-danger">Say why, so the approver knows what to check.</span> : null}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <p className="m-0 text-xs leading-normal text-muted">Exports keep using the codes in use now until someone else approves these.</p>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving" : "Send for approval"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
