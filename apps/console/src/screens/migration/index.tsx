import { useCallback, useEffect, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  MigrationError,
  migrationErrorMessage,
  notConnectedMigrationApi,
  type MigrationApi,
  type Overview,
  type TotalsInput,
  type TrialInput,
} from "./api.ts";
import { Cards, ChecksTable, Panel, PanelRow } from "./Checks.tsx";
import { SignOffs, TotalsForm, TotalsPanel, type FinanceActions } from "./Finance.tsx";
import { BLOCKER_LABELS, formatAmount } from "./labels.ts";
import { createHttpMigrationApi } from "./http.ts";
import { createStubMigrationApi } from "./stub.ts";
import { Problems, TrialForm } from "./Trial.tsx";

function sampleStart(sample: string | null): "empty" | "imported" | "unconfirmed" {
  return sample === "empty" || sample === "unconfirmed" ? sample : "imported";
}

/**
 * Sample figures under `pnpm dev` or a preview build, like sign-in. Otherwise the real routes,
 * or "not connected" in a build that names no API.
 */
function defaultApi(staffId: string | null): MigrationApi {
  if (isStubAuth()) {
    // Under the stub, `?sample=empty` shows the screen before any trial has been run, and
    // `?sample=unconfirmed` shows Finvest 1.0 totals waiting for a second person.
    return createStubMigrationApi(150, sampleStart(new URLSearchParams(window.location.search).get("sample")));
  }
  const base = apiBaseUrl();
  if (!base || !staffId) return notConnectedMigrationApi;
  const http = createHttp(base);
  return createHttpMigrationApi(http, createHttpDirectory(http), staffId);
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: Overview };

const SUMMARY = "Moving clients and investments from Finvest 1.0, checked before anyone is switched.";

function today(now: Date): string {
  // The business day in Kampala, as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(now);
}

export function MigrationScreen({ api, now = () => new Date() }: { api: MigrationApi; now?: () => Date }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [formOpen, setFormOpen] = useState<"trial" | "totals" | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchOverview = useCallback(
    (): Promise<Load> =>
      api.getOverview().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: migrationErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchOverview().then((result) => current && setLoad(result));
    return () => {
      current = false;
    };
  }, [fetchOverview]);

  /** Run one change. A form keeps its own error; anything else is shown above the checks. */
  async function act(change: () => Promise<Overview>, inForm: boolean) {
    setBusy(true);
    setFormError(null);
    setActionError(null);
    try {
      const value = await change();
      setLoad({ state: "ready", value });
      if (inForm) setFormOpen(null);
    } catch (error) {
      (inForm ? setFormError : setActionError)(migrationErrorMessage(error));
      // Someone else changed it first: show what stands now.
      if (error instanceof MigrationError && (error.code === "conflict" || error.code === "stale")) {
        const latest = await fetchOverview();
        if (latest.state === "ready") setLoad(latest);
      }
    } finally {
      setBusy(false);
    }
  }

  const closeForm = () => {
    setFormOpen(null);
    setFormError(null);
  };
  const overview = load.state === "ready" ? load.value : null;
  const mayRun = overview?.allowed.includes("run_trial") ?? false;
  const form =
    formOpen === "trial" ? (
      <TrialForm
        busy={busy}
        error={formError}
        today={today(now())}
        onRun={(input: TrialInput) => void act(() => api.runTrial(input), true)}
        onCancel={closeForm}
      />
    ) : formOpen === "totals" && overview?.trial ? (
      <TotalsForm
        busy={busy}
        error={formError}
        asOf={overview.trial.asOf}
        onEnter={(input: TotalsInput) => void act(() => api.enterTotals(input), true)}
        onCancel={closeForm}
      />
    ) : null;
  const actions: FinanceActions = {
    busy,
    onPropose: (key, reason) => void act(() => api.proposeSignOff(key, reason), false),
    onDecide: (signOffId, approve, reason) => void act(() => api.decideSignOff(signOffId, approve, reason), false),
  };

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Migration" summary={SUMMARY} />
        {mayRun && !formOpen ? <Button onClick={() => setFormOpen("trial")}>Run a trial import</Button> : null}
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the migration
        </p>
      ) : load.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">The migration could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchOverview().then(setLoad);
            }}
          >
            Try again
          </Button>
        </section>
      ) : load.value.trial === null ? (
        <>
          {form}
          <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
            <h2 className="m-0 text-xl font-extrabold">No trial import yet</h2>
            <p className="m-0 text-md leading-normal text-muted">
              Export clients and investments from Finvest 1.0, add anything kept by hand, and save the two sheets as CSV. A
              trial import then lists every problem in them. Nothing is saved and no client is changed.
            </p>
            <p className="m-0 text-md leading-normal text-muted">The columns each sheet needs are in docs/migration.md.</p>
          </section>
        </>
      ) : (
        <Ready
          overview={load.value}
          form={form}
          actionError={actionError}
          actions={actions}
          onOpenTotals={() => setFormOpen("totals")}
          onConfirmTotals={(totalsId) => void act(() => api.confirmTotals(totalsId), false)}
        />
      )}
    </>
  );
}

interface ReadyProps {
  overview: Overview;
  form: React.ReactNode;
  actionError: string | null;
  actions: FinanceActions;
  onOpenTotals: () => void;
  onConfirmTotals: (totalsId: string) => void;
}

function Ready({ overview, form, actionError, actions, onOpenTotals, onConfirmTotals }: ReadyProps) {
  const claims = overview.claims;
  const count = (value: number) => formatAmount(String(value), null);
  return (
    <>
      {form}
      {actionError ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface p-3 text-md font-semibold text-danger">
          {actionError}
        </p>
      ) : null}
      <Cards overview={overview} />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-5">
          <ChecksTable lines={overview.lines} />
          {overview.trial ? <Problems trial={overview.trial} /> : null}
        </div>
        <aside className="flex min-w-0 flex-[2_1_300px] flex-col gap-4">
          <TotalsPanel
            totals={overview.totals}
            mayEnter={overview.allowed.includes("enter_totals")}
            busy={actions.busy}
            onOpenForm={onOpenTotals}
            onConfirm={onConfirmTotals}
          />
          <SignOffs overview={overview} actions={actions} />
          <Panel title="Before the switch">
            <PanelRow name="Trial imports run" value={count(overview.trialsRun)} />
            {overview.blockers.length === 0 ? (
              <p className="m-0 border-t border-divider pt-2.5 text-md font-bold">Everything agrees. The switch can be scheduled.</p>
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
          </Panel>
          <Panel title="Clients claiming their accounts">
            {claims ? (
              <>
                <PanelRow name="Invited by SMS and email" value={count(claims.invited)} />
                <PanelRow name="Claimed and confirmed" value={count(claims.claimedAndConfirmed)} />
                <PanelRow name="Said something is wrong" value={count(claims.saidSomethingIsWrong)} />
                <PanelRow name="Not yet claimed" value={count(claims.notYetClaimed)} />
                <PanelRow name="No phone number, cannot be invited" value={count(claims.cannotBeInvited)} />
              </>
            ) : (
              <p className="m-0 border-t border-divider pt-2.5 text-md leading-normal text-muted">
                Nobody has been invited. Invitations go out by SMS and email after the switch.
              </p>
            )}
            <p className="m-0 text-sm leading-normal text-muted">
              Unclaimed accounts keep earning and mature as before. Staff can call a client and send a fresh link.
            </p>
          </Panel>
        </aside>
      </div>
    </>
  );
}

export default function MigrationPage() {
  const { session } = useAuth();
  const staffId = session?.staffId ?? null;
  const [api] = useState(() => defaultApi(staffId));
  return <MigrationScreen api={api} />;
}
