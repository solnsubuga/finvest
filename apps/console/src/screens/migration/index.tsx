import { useCallback, useEffect, useState } from "react";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { migrationErrorMessage, notConnectedMigrationApi, type MigrationApi, type Overview, type TrialInput } from "./api.ts";
import { Cards, ChecksTable, Differences, Panel, PanelRow } from "./Checks.tsx";
import { BLOCKER_LABELS, formatAmount } from "./labels.ts";
import { createStubMigrationApi } from "./stub.ts";
import { Problems, TrialForm } from "./Trial.tsx";

/** Sample figures under `pnpm dev` or a preview build, like sign-in. Otherwise nothing until the API client exists. */
const defaultApi: MigrationApi = isStubAuth()
  ? // Under the stub, `?sample=empty` shows the screen before any trial has been run.
    createStubMigrationApi(150, new URLSearchParams(window.location.search).get("sample") === "empty" ? "empty" : "imported")
  : notConnectedMigrationApi;

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: Overview };

const SUMMARY = "Moving clients and investments from Finvest 1.0, checked before anyone is switched.";

function today(now: Date): string {
  // The business day in Kampala, as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(now);
}

export function MigrationScreen({ api = defaultApi, now = () => new Date() }: { api?: MigrationApi; now?: () => Date }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [formOpen, setFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [trialError, setTrialError] = useState<string | null>(null);

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

  async function runTrial(input: TrialInput) {
    setBusy(true);
    setTrialError(null);
    try {
      const value = await api.runTrial(input);
      setLoad({ state: "ready", value });
      setFormOpen(false);
    } catch (error) {
      setTrialError(migrationErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const overview = load.state === "ready" ? load.value : null;
  const mayRun = overview?.allowed.includes("run_trial") ?? false;
  const form = formOpen ? (
    <TrialForm
      busy={busy}
      error={trialError}
      today={today(now())}
      onRun={(input) => void runTrial(input)}
      onCancel={() => {
        setFormOpen(false);
        setTrialError(null);
      }}
    />
  ) : null;

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Migration" summary={SUMMARY} />
        {mayRun && !formOpen ? <Button onClick={() => setFormOpen(true)}>Run a trial import</Button> : null}
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
        <Ready overview={load.value} form={form} />
      )}
    </>
  );
}

function Ready({ overview, form }: { overview: Overview; form: React.ReactNode }) {
  const claims = overview.claims;
  const count = (value: number) => formatAmount(String(value), null);
  return (
    <>
      {form}
      <Cards overview={overview} />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-5">
          <ChecksTable lines={overview.lines} />
          {overview.trial ? <Problems trial={overview.trial} /> : null}
        </div>
        <aside className="flex min-w-0 flex-[2_1_300px] flex-col gap-4">
          <Differences overview={overview} />
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
  return <MigrationScreen />;
}
