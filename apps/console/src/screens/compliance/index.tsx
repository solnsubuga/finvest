import { useCallback, useEffect, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { Panel, PanelRow, Pill } from "@/screens/migration/Checks.tsx";
import { formatExact, formatStamp } from "@/screens/products/format.ts";
import {
  complianceErrorMessage,
  notConnectedComplianceApi,
  type Case,
  type CaseDetail,
  type CaseOutcome,
  type CaseSource,
  type CaseStatus,
  type ComplianceApi,
  type Proposal,
  type RegisterFile,
  type Rules,
  type Summary,
  type WaitingPayout,
} from "./api.ts";
import { createHttpComplianceApi } from "./http.ts";
import {
  CATEGORY_LABELS,
  CODE_LABELS,
  OUTCOME_LABELS,
  REASON_LABELS,
  RUN_LABELS,
  SOURCE_LABELS,
  STANDING_LABELS,
  STANDING_TONES,
  codeTitle,
  detailIds,
  detailRows,
} from "./labels.ts";
import { createStubComplianceApi, type StubCase } from "./stub.ts";

const SUMMARY =
  "Sanctions and politically exposed person screening, transaction monitoring, and the cases two people close. Clients never see any of this.";
const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const REASON_LIMIT = 1000;

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

function useLoad<T>(fetch: () => Promise<T>): [Load<T>, () => void] {
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });
  const [round, setRound] = useState(0);
  useEffect(() => {
    let current = true;
    void fetch().then(
      (value) => current && setLoad({ state: "ready", value }),
      (error: unknown) => current && setLoad({ state: "failed", message: complianceErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [fetch, round]);
  return [load, () => setRound((n) => n + 1)];
}

function Failed({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 className="m-0 text-xl font-extrabold">{title}</h2>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </section>
  );
}

function Card({ title, value, note, attention = false }: { title: string; value: string; note: string; attention?: boolean }) {
  return (
    <div className={cn("rounded-lg bg-surface p-4", attention ? "border-2 border-warning" : "border border-border")}>
      <div className="text-sm text-muted">{title}</div>
      <div className="text-2xl font-extrabold tracking-[-0.4px] tabular-nums">{value}</div>
      <div className={cn("text-xs font-bold", attention ? "text-warning-text" : "text-muted")}>{note}</div>
    </div>
  );
}

function Cards({ summary }: { summary: Summary }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
      <Card
        title="Open cases"
        value={String(summary.openCases)}
        note={`${summary.openScreeningCases} screening, ${summary.openMonitoringCases} monitoring`}
        attention={summary.openCases > 0}
      />
      <Card title="Waiting for a second person" value={String(summary.waitingForSecondPerson)} note="Someone else approves each" />
      <Card
        title="Payouts held for screening"
        value={String(summary.payoutsWaiting)}
        note="They move on once the client is clear"
        attention={summary.payoutsWaiting > 0}
      />
      <Card
        title="Screened, last 30 days"
        value={summary.screenedLast30Days.toLocaleString("en-GB")}
        note={
          summary.screeningOn
            ? `${summary.unavailableLast30Days} failed, ${summary.pending} waiting`
            : `${summary.notRunLast30Days} recorded as not run`
        }
      />
      <Card
        title="Reports to the FIA this year"
        value={String(summary.reportsToFiaThisYear)}
        note="Cases closed with a report owed"
      />
    </div>
  );
}

function ScreeningOff() {
  return (
    <p role="status" className="m-0 rounded-xl border-2 border-warning bg-warning-surface px-4 py-3 text-md font-semibold text-warning-text">
      Screening is off. No screening provider is set up yet, so every screen is recorded as not run and payouts do not wait for one. Monitoring
      rules still open cases, and an open screening case still holds a client's payouts.
    </p>
  );
}

function Toggle<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: readonly { value: T; label: string }[]; onChange: (value: T) => void }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-2">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "h-9 rounded-pill border px-3.5 text-sm font-bold",
            value === option.value ? "border-black bg-black text-white" : "border-border-strong bg-surface text-ink",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

const STATUSES: readonly { value: CaseStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
];
const SOURCES: readonly { value: CaseSource | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "screening", label: "Screening" },
  { value: "monitoring", label: "Monitoring" },
];

function clientName(item: { client: { name: string | null } }): string {
  return item.client.name ?? "No identity check yet";
}

function CaseState({ item }: { item: Case }) {
  if (item.status === "closed") {
    return <Pill tone="bg-surface-sunken text-ink">{item.outcome ? OUTCOME_LABELS[item.outcome].title : "Closed"}</Pill>;
  }
  if (item.waiting) return <Pill tone="bg-warning-surface text-warning-text">Waiting for a second person</Pill>;
  return <Pill tone="bg-danger-surface text-danger">Needs a proposal</Pill>;
}

function CasesTable({ cases, selected, onSelect }: { cases: readonly Case[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Compliance cases">
      <table className="w-full min-w-[640px] border-collapse text-md">
        <caption className="sr-only">Compliance cases</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Case", "Client", "Source", "Amount", "Status"].map((heading) => (
              <th key={heading} scope="col" className={cn("px-3 py-3.5 font-bold", heading === "Amount" && "text-right")}>
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cases.map((item) => (
            <tr key={item.id} className={cn("border-t border-divider align-top", selected === item.id && "bg-surface-sunken")}>
              <th scope="row" className="px-3 py-3 text-left">
                <button
                  type="button"
                  onClick={() => onSelect(item.id)}
                  aria-pressed={selected === item.id}
                  className="text-left font-bold underline-offset-2 hover:underline"
                >
                  {codeTitle(item.code)}
                </button>
                <div className="text-xs font-normal text-muted">Opened {formatStamp(item.openedAt)}</div>
              </th>
              <td className="px-3 py-3">
                <div className="font-bold">{clientName(item)}</div>
                <div className="text-xs text-muted tabular-nums">{item.client.accountNumber ?? "No account number yet"}</div>
              </td>
              <td className="px-3 py-3">{SOURCE_LABELS[item.source]}</td>
              <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums">
                {item.amount ? formatExact(item.amount) : <span className="text-muted">{item.source === "screening" ? "Not a payment" : "None"}</span>}
              </td>
              <td className="px-3 py-3">
                <CaseState item={item} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Cases({ api, me, canChange, onChanged }: { api: ComplianceApi; me: string; canChange: boolean; onChanged: () => void }) {
  const [status, setStatus] = useState<CaseStatus>("open");
  const [source, setSource] = useState<CaseSource | "all">("all");
  const [selected, setSelected] = useState<string | null>(null);
  const fetch = useCallback(() => api.listCases({ status, source: source === "all" ? null : source }), [api, status, source]);
  const [load, reload] = useLoad(fetch);

  return (
    <div className="flex flex-wrap items-start gap-5">
      <section aria-label="Cases" className="flex min-w-0 flex-[3_1_520px] flex-col gap-3">
        <div className="flex flex-wrap items-center gap-4">
          <Toggle label="Show cases" value={status} options={STATUSES} onChange={setStatus} />
          <Toggle label="Raised by" value={source} options={SOURCES} onChange={setSource} />
        </div>
        {load.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading cases
          </p>
        ) : load.state === "failed" ? (
          <Failed title="Cases could not be loaded" message={load.message} onRetry={reload} />
        ) : load.value.length === 0 ? (
          <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
            <h2 className="m-0 text-xl font-extrabold">{status === "open" ? "No open cases" : "No closed cases yet"}</h2>
            <p className="m-0 text-md leading-normal text-muted">
              {status === "open"
                ? "A case opens when a screen finds a listed person who may be a client, or a monitoring rule fires on a deposit or payout."
                : "A case closes when one person proposes an outcome and a different person approves it."}
            </p>
          </div>
        ) : (
          <CasesTable cases={load.value} selected={selected} onSelect={setSelected} />
        )}
      </section>
      <aside className="flex min-w-0 flex-[2_1_340px] flex-col gap-4">
        {selected ? (
          <CaseView
            key={selected}
            api={api}
            me={me}
            canChange={canChange}
            caseId={selected}
            onChanged={() => {
              reload();
              onChanged();
            }}
          />
        ) : (
          <Panel title="How a case closes">
            <PanelRow name="Proposes" value="One member of staff, with a reason" />
            <PanelRow name="Approves" value="A different person" />
            <PanelRow name="Sends back" value="With a note, for a new proposal" />
            <PanelRow name="Withdraws" value="Only the proposer" />
            <p className="m-0 text-sm leading-normal text-muted">
              Choose a case to see what raised it. A screening case holds the client&apos;s payouts until it closes. Filing a report to the Financial
              Intelligence Authority happens on the FIA&apos;s own system: write its reference in the reason.
            </p>
          </Panel>
        )}
        <RulesPanel api={api} />
      </aside>
    </div>
  );
}

function share([numerator, denominator]: readonly [number, number]): string {
  if (numerator * 2 === denominator) return "Half";
  return `${numerator} in ${denominator}`;
}

function RulesPanel({ api }: { api: ComplianceApi }) {
  const fetch = useCallback(() => api.rules(), [api]);
  const [load, reload] = useLoad<Rules>(fetch);
  if (load.state === "loading") {
    return (
      <p role="status" className="m-0 text-md text-muted">
        Loading the rules
      </p>
    );
  }
  if (load.state === "failed") return <Failed title="The rules could not be loaded" message={load.message} onRetry={reload} />;
  const rules = load.value;
  const money = (minor: number) => formatExact({ minor, currency: rules.currency });
  return (
    <Panel title="Rules that are on">
      <PanelRow name="One deposit of" value={`${money(rules.largeDepositMinor)} or more`} />
      <PanelRow name="Deposits that add up to that" value={`Within ${rules.splitDepositsDays} days`} />
      <PanelRow name="Many deposits" value={`${rules.manyDepositsCount} or more in ${rules.manyDepositsHours} hours`} />
      <PanelRow
        name="Unusually large deposit"
        value={`${rules.unusualMultiple} times the usual, at least ${money(rules.unusualFloorMinor)}`}
      />
      <PanelRow
        name="Quick in and out"
        value={`${share(rules.quickWithdrawalShare)} of what came in paid out within ${rules.quickWithdrawalDays} days, from ${money(rules.quickWithdrawalFloorMinor)}`}
      />
      <PanelRow name="Clients screened again" value={`Every ${rules.rescreenEveryDays} days`} />
      <p className="m-0 text-sm leading-normal text-muted">
        These are read from the rules as they run. The usual deposit is worked out from the last {rules.unusualLookbackDays} days, once a client has made{" "}
        {rules.unusualHistory}. Only {rules.currency} payments are checked.
      </p>
    </Panel>
  );
}

/** Hands the browser the exported file. */
function saveFile({ file, filename }: RegisterFile) {
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ExportRegister({ api, save }: { api: ComplianceApi; save: (file: RegisterFile) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setBusy(true);
    setError(null);
    try {
      save(await api.exportRegister());
    } catch (failure) {
      setError(complianceErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col items-end gap-1.5">
      <Button variant="secondary" disabled={busy} onClick={() => void run()}>
        {busy ? "Preparing the register" : "Export register"}
      </Button>
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function ProposalLine({ proposal }: { proposal: Proposal }) {
  const decided =
    proposal.status === "approved" ? "Approved by " : proposal.status === "sent_back" ? "Sent back by " : proposal.status === "withdrawn" ? "Withdrawn by " : null;
  return (
    <li className="flex flex-col gap-1 border-t border-divider py-2.5 text-sm">
      <div className="font-bold">{OUTCOME_LABELS[proposal.outcome].title}</div>
      <div className="text-muted">
        Proposed by {proposal.proposedByName}, {formatStamp(proposal.proposedAt)}. <span className="text-ink">{proposal.reason}</span>
      </div>
      {decided && proposal.decidedAt ? (
        <div className="text-muted">
          {decided}
          {proposal.decidedByName ?? "a member of staff"}, {formatStamp(proposal.decidedAt)}
          {proposal.decisionNote ? (
            <>
              . <span className="text-ink">{proposal.decisionNote}</span>
            </>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function CaseView({ api, me, canChange, caseId, onChanged }: { api: ComplianceApi; me: string; canChange: boolean; caseId: string; onChanged: () => void }) {
  const fetch = useCallback(() => api.getCase(caseId), [api, caseId]);
  const [load, reload] = useLoad(fetch);
  // What the last command answered, until the case is read again.
  const [answered, setAnswered] = useState<CaseDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shown = answered ?? (load.state === "ready" ? load.value : null);

  async function run(step: () => Promise<CaseDetail>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      setAnswered(await step());
      onChanged();
      return true;
    } catch (failure) {
      setError(complianceErrorMessage(failure));
      setAnswered(null);
      reload();
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!shown) {
    if (load.state === "failed") return <Failed title="The case could not be loaded" message={load.message} onRetry={reload} />;
    return (
      <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
        Loading the case
      </p>
    );
  }
  const { case: item, run: screen, matches, detail, proposals, standing } = shown;
  const waiting = item.waiting;
  const rows = detailRows(detail);
  const ids = detailIds(detail);

  return (
    <section aria-label={`Case: ${codeTitle(item.code)}, ${clientName(item)}`} className="flex flex-col gap-4">
      <Panel title={codeTitle(item.code)}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-md font-bold">{clientName(item)}</span>
          <CaseState item={item} />
        </div>
        <p className="m-0 text-sm leading-normal text-muted">{CODE_LABELS[item.code]?.hint ?? "Raised by a rule."}</p>
        <PanelRow name="Account number" value={item.client.accountNumber ?? "None yet"} />
        <PanelRow name="Opened" value={formatStamp(item.openedAt)} />
        {item.closedAt ? <PanelRow name="Closed" value={formatStamp(item.closedAt)} /> : null}
        {standing ? (
          <PanelRow name="Where the client stands" value={<Pill tone={STANDING_TONES[standing.outcome]}>{STANDING_LABELS[standing.outcome]}</Pill>} />
        ) : null}
      </Panel>
      {item.source === "screening" ? (
        <Panel title="What the screen found">
          {screen ? (
            <>
              <PanelRow name="Screened" value={`${REASON_LABELS[screen.reason]}, ${formatStamp(screen.finishedAt ?? screen.requestedAt)}`} />
              <PanelRow name="Result" value={RUN_LABELS[screen.status]} />
            </>
          ) : null}
          <ul className="m-0 flex list-none flex-col p-0" aria-label="Listed entities">
            {matches.map((match) => (
              <li key={match.entityId} className="flex flex-col gap-1 border-t border-divider py-2.5 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-bold">{match.name}</span>
                  <span className="font-bold tabular-nums">{match.strength}% match</span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Pill tone={match.category === "sanctions" ? "bg-danger-surface text-danger" : "bg-surface-sunken text-ink"}>
                    {CATEGORY_LABELS[match.category]}
                  </Pill>
                  {match.known ? <Pill tone="bg-success-surface text-success">Already decided for this client</Pill> : null}
                </div>
                <div className="text-xs text-muted">
                  {match.sources.join(", ")}. Provider ID <span className="font-mono">{match.entityId}</span>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      ) : (
        <Panel title="What the rule saw">
          {rows.map((row) => (
            <PanelRow key={row.name} name={row.name} value={row.value} />
          ))}
          {ids.length > 0 ? (
            <div className="flex flex-col gap-1 border-t border-divider pt-2.5 text-sm">
              <span className="text-muted">Transactions</span>
              {ids.map((id) => (
                <span key={id} className="font-mono text-xs break-all">
                  {id}
                </span>
              ))}
            </div>
          ) : null}
        </Panel>
      )}
      <Panel title="Closing the case">
        {error ? (
          <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
            {error}
          </p>
        ) : null}
        {item.status === "closed" ? (
          <p className="m-0 text-sm text-ink">
            Closed as {item.outcome ? OUTCOME_LABELS[item.outcome].title.toLowerCase() : "decided"}. A closed case never changes.
          </p>
        ) : null}
        {waiting ? (
          <Waiting
            proposal={waiting}
            mine={shown.yours || waiting.proposedBy === me}
            canChange={canChange}
            canDecide={shown.canDecide}
            busy={busy}
            onApprove={() => run(() => api.approve(waiting.id))}
            onSendBack={(note) => run(() => api.sendBack(waiting.id, note))}
            onWithdraw={() => run(() => api.withdraw(waiting.id))}
          />
        ) : null}
        {item.status === "open" && !waiting && canChange && shown.canPropose ? (
          <ProposeForm outcomes={shown.outcomes} busy={busy} onPropose={(outcome, reason) => run(() => api.propose(item.id, outcome, reason))} />
        ) : null}
        {item.status === "open" && !waiting && !canChange ? (
          <p className="m-0 text-sm text-muted">Someone with the compliance role proposes how this case closes.</p>
        ) : null}
        {proposals.length > 0 ? (
          <ul className="m-0 flex list-none flex-col p-0" aria-label="Proposals">
            {proposals.map((proposal) => (
              <ProposalLine key={proposal.id} proposal={proposal} />
            ))}
          </ul>
        ) : null}
      </Panel>
    </section>
  );
}

interface WaitingProps {
  proposal: Proposal;
  mine: boolean;
  canChange: boolean;
  canDecide: boolean;
  busy: boolean;
  onApprove: () => Promise<boolean>;
  onSendBack: (note: string) => Promise<boolean>;
  onWithdraw: () => Promise<boolean>;
}

function Waiting({ proposal, mine, canChange, canDecide, busy, onApprove, onSendBack, onWithdraw }: WaitingProps) {
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const empty = note.trim().length === 0;

  return (
    <div className="flex flex-col gap-2 rounded-lg bg-surface-sunken p-3">
      <div className="text-sm">
        <span className="font-bold">{OUTCOME_LABELS[proposal.outcome].title}</span>, proposed by {mine ? "you" : proposal.proposedByName}
      </div>
      <p className="m-0 text-sm text-ink">{proposal.reason}</p>
      {!canChange ? <p className="m-0 text-sm text-muted">Someone with the compliance role approves it.</p> : null}
      {canChange && mine ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted">Someone else approves it.</span>
          <Button variant="secondary" disabled={busy} onClick={() => void onWithdraw()}>
            {busy ? "Saving" : "Withdraw it"}
          </Button>
        </div>
      ) : null}
      {canChange && !mine && canDecide && !sendingBack ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-sm text-muted">
            {proposal.outcome === "sanctioned"
              ? "Approving confirms a sanctions match: the client's payouts stay held for good, and a report is owed to the FIA."
              : "Approving closes the case with this outcome. You will be asked to confirm it is you."}
          </p>
          <div className="flex gap-2">
            <Button disabled={busy} onClick={() => void onApprove()}>
              {busy ? "Saving" : "Approve"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(true)}>
              Send back
            </Button>
          </div>
        </div>
      ) : null}
      {sendingBack ? (
        <form
          aria-label="Send the proposal back"
          className="flex flex-col gap-2 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (empty) return;
            void onSendBack(note.trim()).then((done) => done && setSendingBack(false));
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            What needs checking?
            <textarea value={note} rows={2} maxLength={REASON_LIMIT} aria-invalid={tried && empty} onChange={(event) => setNote(event.target.value)} className={INPUT} />
          </label>
          {tried && empty ? <p className="m-0 text-xs font-bold text-danger">Say why, so the proposer knows what to check.</p> : null}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving" : "Send it back"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

function ProposeForm({ outcomes, busy, onPropose }: { outcomes: readonly CaseOutcome[]; busy: boolean; onPropose: (outcome: CaseOutcome, reason: string) => Promise<boolean> }) {
  const [outcome, setOutcome] = useState<CaseOutcome | null>(null);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const empty = reason.trim().length === 0;

  return (
    <form
      aria-label="Propose an outcome"
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (!outcome || empty) return;
        void onPropose(outcome, reason.trim());
      }}
    >
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 text-sm font-bold">How should it close?</legend>
        {outcomes.map((option) => (
          <label key={option} className={cn("flex gap-2.5 rounded-lg border p-3 text-sm", outcome === option ? "border-black" : "border-border")}>
            <input type="radio" name="outcome" checked={outcome === option} onChange={() => setOutcome(option)} />
            <span className="flex flex-col gap-0.5">
              <span className="font-bold">{OUTCOME_LABELS[option].title}</span>
              <span className="text-muted">{OUTCOME_LABELS[option].hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {tried && !outcome ? <p className="m-0 text-xs font-bold text-danger">Choose an outcome.</p> : null}
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        Why?
        <span className="font-normal text-muted">Staff only, for the person who approves it and the audit trail.</span>
        <textarea value={reason} rows={3} maxLength={REASON_LIMIT} aria-invalid={tried && empty} onChange={(event) => setReason(event.target.value)} className={INPUT} />
      </label>
      {tried && empty ? <p className="m-0 text-xs font-bold text-danger">Write the reason.</p> : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving" : "Propose"}
        </Button>
        <span className="text-sm text-muted">Nothing closes until someone else approves it.</span>
      </div>
    </form>
  );
}

function WaitingPayouts({ api, round }: { api: ComplianceApi; round: number }) {
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `round` reloads the list after a case changes.
  const fetch = useCallback(() => api.waitingPayouts(), [api, round]);
  const [load, reload] = useLoad<readonly WaitingPayout[]>(fetch);
  return (
    <section aria-label="Payouts held for screening" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 className="m-0 text-xl font-extrabold">Payouts held for screening</h2>
        <p className="m-0 text-md text-muted">
          Their money is reserved and nothing is sent. Each moves on by itself once the client is screened clear. Nobody releases one by hand; staff
          may decline one from the review queue. The client sees it as processing.
        </p>
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading payouts
        </p>
      ) : load.state === "failed" ? (
        <Failed title="Payouts could not be loaded" message={load.message} onRetry={reload} />
      ) : load.value.length === 0 ? (
        <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">No payout is waiting for a screen.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Payouts waiting">
          <table className="w-full min-w-[640px] border-collapse text-md">
            <caption className="sr-only">Payouts held for screening</caption>
            <thead>
              <tr className="text-left text-xs text-muted">
                {["Client", "Amount", "Asked for", "Where the client stands"].map((heading) => (
                  <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {load.value.map((payout) => (
                <tr key={payout.id} className="border-t border-divider align-top">
                  <th scope="row" className="px-3 py-3 text-left">
                    <div className="font-bold">{clientName(payout)}</div>
                    <div className="text-xs font-normal text-muted tabular-nums">{payout.client.accountNumber ?? "No account number yet"}</div>
                  </th>
                  <td className="px-3 py-3 whitespace-nowrap">
                    <div className="font-bold tabular-nums">{formatExact(payout.amount)}</div>
                    <div className="text-xs text-muted">{payout.kind === "bank" ? "To a bank account" : "To mobile money"}</div>
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">{formatStamp(payout.createdAt)}</td>
                  <td className="px-3 py-3">
                    {payout.standing ? (
                      <Pill tone={STANDING_TONES[payout.standing.outcome]}>{STANDING_LABELS[payout.standing.outcome]}</Pill>
                    ) : (
                      <Pill tone="bg-surface-sunken text-ink">Never screened</Pill>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function ComplianceScreen({
  api,
  me,
  canChange,
  save = saveFile,
}: {
  api: ComplianceApi;
  me: string;
  canChange: boolean;
  save?: (file: RegisterFile) => void;
}) {
  const [round, setRound] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `round` reloads the figures after a case changes.
  const fetchSummary = useCallback(() => api.summary(), [api, round]);
  const [summary, reloadSummary] = useLoad(fetchSummary);

  if (summary.state === "failed") {
    return (
      <>
        <PageHeader title="Compliance" summary={SUMMARY} />
        <Failed title="Compliance could not be loaded" message={summary.message} onRetry={reloadSummary} />
      </>
    );
  }
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title="Compliance" summary={SUMMARY} />
        <ExportRegister api={api} save={save} />
      </div>
      {summary.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading compliance
        </p>
      ) : (
        <>
          {summary.value.screeningOn ? null : <ScreeningOff />}
          <Cards summary={summary.value} />
        </>
      )}
      <Cases api={api} me={me} canChange={canChange} onChanged={() => setRound((n) => n + 1)} />
      <WaitingPayouts api={api} round={round} />
    </>
  );
}

function stubCase(): StubCase {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" || sample === "off" ? sample : "default";
}

export default function CompliancePage() {
  const { session } = useAuth();
  const [api] = useState<ComplianceApi>(() => {
    // Under the stub, `?sample=empty`, `?sample=off` (no screening provider) and `?sample=error` show those states.
    if (isStubAuth() && session) return createStubComplianceApi({ id: session.staffId, name: session.name }, stubCase());
    const base = apiBaseUrl();
    return base ? createHttpComplianceApi(createHttp(base)) : notConnectedComplianceApi;
  });
  if (!session) return null;
  return <ComplianceScreen api={api} me={session.staffId} canChange={can(session, "compliance_cases.change")} />;
}
