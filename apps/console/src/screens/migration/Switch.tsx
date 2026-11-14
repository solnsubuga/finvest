import { useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { NotCarried, Overview, ResolutionAction, Switch, SwitchInput } from "./api.ts";
import { Panel, PanelRow, Pill } from "./Checks.tsx";
import { ReasonForm } from "./Finance.tsx";
import { BLOCKER_LABELS, formatAmount, formatDay, formatMoment, notCarriedLabel } from "./labels.ts";
import { FILE, sheetProblem } from "./Trial.tsx";

export interface SwitchActions {
  busy: boolean;
  onOpenProposal: () => void;
  onDecide: (switchId: string, approve: boolean, reason?: string) => void;
  onPropose: (switchId: string, legacyClientRef: string, action: ResolutionAction, reason: string) => void;
  onDecideResolution: (resolutionId: string, approve: boolean, reason?: string) => void;
}

type Errors = Partial<Record<"clients" | "investments", string | undefined>>;

/** The same two sheets the latest trial checked. The backend refuses any others. */
export function SwitchForm({ busy, error, asOf, onPropose, onCancel }: { busy: boolean; error: string | null; asOf: string; onPropose: (input: SwitchInput) => void; onCancel: () => void }) {
  const id = useId();
  const [clients, setClients] = useState<File | null>(null);
  const [investments, setInvestments] = useState<File | null>(null);
  const [errors, setErrors] = useState<Errors>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const found: Errors = {
      clients: sheetProblem(clients, "Choose the clients sheet."),
      investments: sheetProblem(investments, "Choose the investments sheet."),
    };
    setErrors(found);
    if (clients && investments && !found.clients && !found.investments) onPropose({ clients, investments });
  }

  const field = (name: keyof Errors) => ({
    id: `${id}-${name}`,
    "aria-invalid": errors[name] ? true : undefined,
    "aria-describedby": errors[name] ? `${id}-${name}-error` : undefined,
  });
  const message = (name: keyof Errors) =>
    errors[name] ? (
      <p id={`${id}-${name}-error`} className="m-0 text-sm font-semibold text-danger">
        {errors[name]}
      </p>
    ) : null;

  return (
    <form noValidate aria-label="Propose the switch" onSubmit={submit} className="flex flex-col gap-4 rounded-xl border-2 border-black bg-surface p-5">
      <div>
        <h2 className="m-0 text-xl font-extrabold">Propose the switch</h2>
        <p className="m-0 text-md leading-normal text-muted">
          Choose the same two sheets the trial as of {formatDay(asOf)} checked, unchanged. A second person approves the switch,
          and only then is every client given an account. Nothing is created now.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-clients`}>Clients sheet (CSV)</Label>
          <input type="file" accept=".csv,text/csv" className={FILE} onChange={(event) => setClients(event.target.files?.[0] ?? null)} {...field("clients")} />
          {message("clients")}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-investments`}>Investments sheet (CSV)</Label>
          <input type="file" accept=".csv,text/csv" className={FILE} onChange={(event) => setInvestments(event.target.files?.[0] ?? null)} {...field("investments")} />
          {message("investments")}
        </div>
      </div>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface p-3 text-md font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Checking the sheets" : "Propose the switch"}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col gap-2 rounded-md bg-surface-sunken p-3 text-sm leading-snug">{children}</div>;
}

function Decision({ switchView, mayDecide, actions }: { switchView: Switch; mayDecide: boolean; actions: SwitchActions }) {
  const [rejecting, setRejecting] = useState(false);
  return (
    <Note>
      <span>
        <span className="font-bold">Proposed by {switchView.mine ? "you" : switchView.proposedBy}</span> on {formatMoment(switchView.proposedAt)}, with
        the sheets of the trial as of {formatDay(switchView.asOf)}.
      </span>
      {switchView.mine ? <span className="text-muted">Someone else approves it.</span> : null}
      {!switchView.mine && mayDecide && !rejecting ? (
        <>
          <span className="text-muted">Approving checks everything again and gives every client an account, all or none.</span>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => actions.onDecide(switchView.id, true)} disabled={actions.busy} className="h-9 px-3">
              Approve the switch
            </Button>
            <Button variant="secondary" onClick={() => setRejecting(true)} disabled={actions.busy} className="h-9 px-3">
              Reject
            </Button>
          </div>
        </>
      ) : null}
      {rejecting ? (
        <ReasonForm
          label="Why reject the switch"
          submit="Reject the switch"
          busy={actions.busy}
          onSubmit={(reason) => actions.onDecide(switchView.id, false, reason)}
          onCancel={() => setRejecting(false)}
        />
      ) : null}
    </Note>
  );
}

/** What blocks the switch, the proposal and its decision, in the side column. */
export function SwitchPanel({ overview, actions }: { overview: Overview; actions: SwitchActions }) {
  const proposal = overview.switch;
  const mayPropose = overview.allowed.includes("propose_switch");
  const mayDecide = overview.allowed.includes("decide_switch");
  const open = proposal === null || proposal.state === "rejected";
  const count = (value: number) => formatAmount(String(value), null);
  return (
    <Panel title={proposal?.state === "approved" ? "The switch" : "Before the switch"}>
      {proposal?.state === "approved" ? (
        <>
          <PanelRow name="Approved by" value={proposal.decidedBy ?? "A colleague"} />
          <PanelRow name="Accounts created" value={count(proposal.clientsCarried)} />
          <PanelRow name="Not carried over yet" value={count(proposal.notCarried.length)} />
          <p className="m-0 text-sm leading-normal text-muted">
            Each client&apos;s identity, investments and claim link are carried over one at a time. A client who cannot be is
            listed under Not carried over.
          </p>
        </>
      ) : (
        <>
          <PanelRow name="Trial imports run" value={count(overview.trialsRun)} />
          {overview.blockers.length === 0 ? (
            <p className="m-0 border-t border-divider pt-2.5 text-md font-bold">
              {proposal?.state === "proposed" ? "Everything agrees. The switch waits for a second person." : "Everything agrees. The switch can be proposed."}
            </p>
          ) : (
            <ul aria-label="Still to do before the switch" className="m-0 flex list-none flex-col p-0">
              {overview.blockers.map((blocker) => (
                <li key={blocker} className="flex items-center gap-2.5 border-t border-divider py-2.5 text-md font-bold">
                  <span aria-hidden="true" className="size-2.5 shrink-0 rounded-pill bg-warning" />
                  {BLOCKER_LABELS[blocker]}
                </li>
              ))}
            </ul>
          )}
          {proposal?.state === "rejected" ? (
            <Note>
              <span>
                <span className="font-bold">Proposed by {proposal.mine ? "you" : proposal.proposedBy}</span> on {formatMoment(proposal.proposedAt)}.
              </span>
              <span>
                <span className="font-bold text-danger">Rejected by {proposal.decidedBy}:</span> {proposal.decisionReason}
              </span>
            </Note>
          ) : null}
          {proposal?.state === "proposed" ? <Decision switchView={proposal} mayDecide={mayDecide} actions={actions} /> : null}
          {open && mayPropose && overview.blockers.length === 0 ? (
            <Button onClick={actions.onOpenProposal} disabled={actions.busy} className="h-9 self-start px-3">
              {proposal ? "Propose the switch again" : "Propose the switch"}
            </Button>
          ) : null}
        </>
      )}
    </Panel>
  );
}

const STATES: Record<NotCarried["state"], { label: string; tone: string }> = {
  noted: { label: "Needs a person", tone: "bg-warning-surface text-warning-text" },
  retry_proposed: { label: "Retry awaiting approval", tone: "bg-surface-sunken text-ink" },
  close_proposed: { label: "Close awaiting approval", tone: "bg-surface-sunken text-ink" },
  retrying: { label: "Retrying", tone: "bg-success-surface text-success" },
};

function NotCarriedRow({ client, switchId, overview, actions }: { client: NotCarried; switchId: string; overview: Overview; actions: SwitchActions }) {
  const [open, setOpen] = useState<ResolutionAction | "reject" | null>(null);
  const mayPropose = overview.allowed.includes("propose_resolution");
  const mayDecide = overview.allowed.includes("decide_resolution");
  const state = STATES[client.state];
  const pending = client.state === "retry_proposed" || client.state === "close_proposed";
  return (
    <li className="flex flex-col gap-2 border-t border-divider py-3 text-md">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <span className="font-bold">Finvest 1.0 client {client.legacyClientRef}</span>
          <span className="text-sm text-muted">
            {notCarriedLabel(client.code)}. {client.attempt > 1 ? `Tried ${client.attempt} times, noted` : "Noted"} {formatMoment(client.notedAt)}.
          </span>
        </div>
        <Pill tone={state.tone}>{state.label}</Pill>
      </div>
      {pending && client.resolutionId ? (
        <Note>
          <span>
            <span className="font-bold">
              {client.state === "retry_proposed" ? "Retry" : "Close"} proposed by {client.mine ? "you" : (client.proposedBy ?? "a colleague")}:
            </span>{" "}
            {client.reason}
          </span>
          {client.mine ? <span className="text-muted">Someone else approves it.</span> : null}
          {!client.mine && mayDecide && open === null ? (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => actions.onDecideResolution(client.resolutionId!, true)} disabled={actions.busy} className="h-9 px-3">
                Approve
              </Button>
              <Button variant="secondary" onClick={() => setOpen("reject")} disabled={actions.busy} className="h-9 px-3">
                Reject
              </Button>
            </div>
          ) : null}
          {open === "reject" ? (
            <ReasonForm
              label={`Why reject this for client ${client.legacyClientRef}`}
              submit="Reject"
              busy={actions.busy}
              onSubmit={(reason) => actions.onDecideResolution(client.resolutionId!, false, reason)}
              onCancel={() => setOpen(null)}
            />
          ) : null}
        </Note>
      ) : null}
      {client.state === "retrying" ? (
        <p className="m-0 text-sm leading-normal text-muted">Approved. The client is tried again with every check.</p>
      ) : null}
      {client.state === "noted" && mayPropose ? (
        open === "retry" || open === "close" ? (
          <ReasonForm
            label={
              open === "retry"
                ? `What was put right for client ${client.legacyClientRef}`
                : `Why client ${client.legacyClientRef} is looked after by hand`
            }
            submit={open === "retry" ? "Propose a retry" : "Propose closing"}
            busy={actions.busy}
            onSubmit={(reason) => actions.onPropose(switchId, client.legacyClientRef, open, reason)}
            onCancel={() => setOpen(null)}
          />
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setOpen("retry")} disabled={actions.busy} className="h-9 px-3">
              Retry
            </Button>
            <Button variant="secondary" onClick={() => setOpen("close")} disabled={actions.busy} className="h-9 px-3">
              Close
            </Button>
          </div>
        )
      ) : null}
    </li>
  );
}

/** Each client the worker could not carry over, with retry and close, each approved by a second person. */
export function NotCarriedList({ overview, actions }: { overview: Overview; actions: SwitchActions }) {
  const proposal = overview.switch;
  if (proposal?.state !== "approved") return null;
  return (
    <section aria-labelledby="not-carried" className="flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="not-carried" className="m-0 text-lg font-extrabold">
          Not carried over
        </h2>
        <span className="text-sm font-bold text-muted tabular-nums">{formatAmount(String(proposal.notCarried.length), null)}</span>
      </div>
      {proposal.notCarried.length === 0 ? (
        <p className="m-0 text-md leading-normal text-muted">Every client is carried over or closed. The sealed sheets are removed once the last one is done.</p>
      ) : (
        <ul aria-label="Clients not carried over" className="m-0 flex list-none flex-col p-0">
          {proposal.notCarried.map((client) => (
            <NotCarriedRow key={client.legacyClientRef} client={client} switchId={proposal.id} overview={overview} actions={actions} />
          ))}
        </ul>
      )}
      <p className="m-0 text-sm leading-normal text-muted">
        Nothing of a client listed here was kept: no identity, investment or link. Put the cause right first, then propose a
        retry, or close them to look after them by hand. A different person approves either one.
      </p>
    </section>
  );
}
