import { useCallback, useEffect, useState } from "react";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { Panel, PanelRow, Pill } from "@/screens/migration/Checks.tsx";
import { goalsErrorMessage, notConnectedGoalsApi, type Goal, type GoalDetail, type GoalsApi, type GoalStatus, type SavingStatus, type Standing } from "./api.ts";
import { CATEGORY_LABELS, formatDay, formatShillings, SAVING_LABELS, STANDING_LABELS } from "./labels.ts";
import { GroupGoals } from "./Groups.tsx";
import { notConnectedGroupsApi, type GroupsApi } from "./groupsApi.ts";
import { createStubGroupsApi } from "./groupsStub.ts";
import { createStubGoalsApi, type StubCase } from "./stub.ts";

function today(now: Date): string {
  // The business day in Kampala, as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(now);
}

function stubCase(): StubCase {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" ? sample : "sample";
}

/** Sample goals under `pnpm dev` or a preview build, like sign-in. `?sample=empty` and `?sample=error` show those states. */
const defaultApi: GoalsApi = isStubAuth() ? createStubGoalsApi(today(new Date()), 150, stubCase()) : notConnectedGoalsApi;
const defaultGroupsApi: GroupsApi = isStubAuth() ? createStubGroupsApi(today(new Date()), 150, stubCase()) : notConnectedGroupsApi;

type View = "goals" | "groups";

const VIEWS: readonly { value: View; label: string }[] = [
  { value: "goals", label: "Goals" },
  { value: "groups", label: "Group goals" },
];

function initialView(): View {
  return new URLSearchParams(window.location.search).get("view") === "groups" ? "groups" : "goals";
}

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };
type Filter = GoalStatus | "all";

const SUMMARY = "What clients are saving towards, alone or together, and the investments each goal is made of.";

const FILTERS: readonly { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Open" },
  { value: "closed", label: "Closed" },
];

const STANDING_TONES: Record<Standing, string> = {
  reached: "bg-success-surface text-success",
  on_track: "bg-success-surface text-success",
  behind: "bg-warning-surface text-warning-text",
  no_date: "bg-surface-sunken text-ink",
};

const SAVING_TONES: Record<SavingStatus, string> = {
  awaiting_payment: "bg-warning-surface text-warning-text",
  active: "bg-success-surface text-success",
  matured: "bg-surface-sunken text-muted",
  cancelled: "bg-surface-sunken text-muted",
};

export function GoalsScreen({ api = defaultApi, groupsApi = defaultGroupsApi }: { api?: GoalsApi; groupsApi?: GroupsApi }) {
  const [view, setView] = useState<View>(initialView);

  return (
    <>
      <PageHeader title="Goals" summary={SUMMARY} />
      <div role="group" aria-label="View" className="flex w-fit gap-1 rounded-pill border border-border bg-surface p-1">
        {VIEWS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={view === option.value}
            onClick={() => setView(option.value)}
            className={cn("h-9 rounded-pill px-4 text-sm font-bold", view === option.value ? "bg-black text-white" : "text-ink")}
          >
            {option.label}
          </button>
        ))}
      </div>
      {view === "goals" ? <OwnGoals api={api} /> : <GroupGoals api={groupsApi} />}
    </>
  );
}

function OwnGoals({ api }: { api: GoalsApi }) {
  const [load, setLoad] = useState<Load<readonly Goal[]>>({ state: "loading" });
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<string | null>(null);

  const fetchGoals = useCallback(
    (): Promise<Load<readonly Goal[]>> =>
      api.listGoals(null).then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: goalsErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchGoals().then((result) => current && setLoad(result));
    return () => {
      current = false;
    };
  }, [fetchGoals]);

  return (
    <>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading goals
        </p>
      ) : load.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Goals could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchGoals().then(setLoad);
            }}
          >
            Try again
          </Button>
        </section>
      ) : (
        <Ready goals={load.value} api={api} filter={filter} onFilter={setFilter} selected={selected} onSelect={setSelected} />
      )}
    </>
  );
}

function Card({ title, value, note }: { title: string; value: string; note: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="text-sm text-muted">{title}</div>
      <div className="text-2xl font-extrabold tracking-[-0.4px] tabular-nums">{value}</div>
      <div className="text-xs font-bold text-muted">{note}</div>
    </div>
  );
}

function Ready({
  goals,
  api,
  filter,
  onFilter,
  selected,
  onSelect,
}: {
  goals: readonly Goal[];
  api: GoalsApi;
  filter: Filter;
  onFilter: (filter: Filter) => void;
  selected: string | null;
  onSelect: (goalId: string) => void;
}) {
  const open = goals.filter((goal) => goal.status === "active");
  const dated = open.filter((goal) => goal.targetDate !== null).length;
  const clients = new Set(open.map((goal) => goal.clientId)).size;
  const shown = filter === "all" ? goals : goals.filter((goal) => goal.status === filter);

  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
        <Card title="Open goals" value={String(open.length)} note={`Set by ${clients} ${clients === 1 ? "client" : "clients"}`} />
        <Card title="With a date" value={String(dated)} note={`${open.length - dated} with no date`} />
        <Card title="Closed" value={String(goals.length - open.length)} note="Their investments keep running" />
      </div>
      <div className="flex flex-wrap items-start gap-5">
        <section aria-label="Goals" className="flex min-w-0 flex-[3_1_560px] flex-col gap-3">
          <div role="group" aria-label="Show goals" className="flex flex-wrap gap-2">
            {FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={filter === option.value}
                onClick={() => onFilter(option.value)}
                className={cn(
                  "h-9 rounded-pill border px-3.5 text-sm font-bold",
                  filter === option.value ? "border-black bg-black text-white" : "border-border-strong bg-surface text-ink",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          {goals.length === 0 ? (
            <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
              <h2 className="m-0 text-xl font-extrabold">No goals yet</h2>
              <p className="m-0 text-md leading-normal text-muted">
                A goal appears here when a client sets one up in the app. Each saving towards it is an ordinary investment.
              </p>
            </div>
          ) : shown.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">No goal is {filter === "active" ? "open" : "closed"}.</p>
          ) : (
            <GoalsTable goals={shown} selected={selected} onSelect={onSelect} />
          )}
        </section>
        <aside className="flex min-w-0 flex-[2_1_320px] flex-col gap-4">
          {selected ? (
            <Detail key={selected} api={api} goalId={selected} />
          ) : (
            <Panel title="How a goal works">
              <PanelRow name="Money held" value="None: a goal is a target" />
              <PanelRow name="Each saving" value="A new investment, paid as usual" />
              <PanelRow name="At maturity" value="Rolls over and stays in the goal" />
              <PanelRow name="Closing" value="The client's choice, and final" />
              <p className="m-0 text-sm leading-normal text-muted">
                This screen only reads. Choose a goal to see its progress and the investments it is made of. Only the client changes or
                closes a goal, and closing moves no money.
              </p>
            </Panel>
          )}
        </aside>
      </div>
    </>
  );
}

function GoalsTable({ goals, selected, onSelect }: { goals: readonly Goal[]; selected: string | null; onSelect: (goalId: string) => void }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[720px] border-collapse text-md">
        <caption className="sr-only">Client goals</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Goal", "Client", "Target", "Invested in", "Status"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {goals.map((goal) => (
            <tr key={goal.id} className={cn("border-t border-divider align-top", selected === goal.id && "bg-surface-sunken")}>
              <th scope="row" className="px-3 py-3 text-left">
                <button type="button" onClick={() => onSelect(goal.id)} aria-pressed={selected === goal.id} className="text-left font-bold underline-offset-2 hover:underline">
                  {goal.name}
                </button>
                <div className="text-xs font-normal text-muted">
                  {CATEGORY_LABELS[goal.category]}, set up {formatDay(goal.createdOn)}
                </div>
              </th>
              <td className="px-3 py-3">
                <div className="font-bold">{goal.clientName}</div>
                <div className="text-xs text-muted tabular-nums">{goal.accountNumber}</div>
              </td>
              <td className="px-3 py-3 whitespace-nowrap">
                <div className="font-bold tabular-nums">{formatShillings(goal.target)}</div>
                <div className="text-xs text-muted">{goal.targetDate ? `By ${formatDay(goal.targetDate)}` : "No date"}</div>
              </td>
              <td className="px-3 py-3">{goal.productName}</td>
              <td className="px-3 py-3">
                {goal.status === "active" ? (
                  <Pill tone="bg-success-surface text-success">Open</Pill>
                ) : (
                  <>
                    <Pill tone="bg-surface-sunken text-muted">Closed</Pill>
                    {goal.closedOn ? <div className="mt-1.5 text-xs text-muted">On {formatDay(goal.closedOn)}</div> : null}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ api, goalId }: { api: GoalsApi; goalId: string }) {
  const [load, setLoad] = useState<Load<GoalDetail>>({ state: "loading" });

  useEffect(() => {
    let current = true;
    void api.getGoal(goalId).then(
      (value) => current && setLoad({ state: "ready", value }),
      (error: unknown) => current && setLoad({ state: "failed", message: goalsErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [api, goalId]);

  if (load.state === "loading") {
    return (
      <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
        Loading the goal
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <section role="alert" className="rounded-xl border border-border bg-surface p-5 text-md text-muted">
        {load.message}
      </section>
    );
  }
  const { goal, progress, savings } = load.value;
  return (
    <section aria-label={`Goal: ${goal.name}`} className="flex flex-col gap-4">
      <Panel title={goal.name}>
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-2xl font-extrabold tabular-nums">{formatShillings(progress.value)}</span>
          <Pill tone={STANDING_TONES[progress.standing]}>{STANDING_LABELS[progress.standing]}</Pill>
        </div>
        <div
          role="progressbar"
          aria-label="Progress towards the target"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress.percent}
          className="h-2 overflow-hidden rounded-pill bg-surface-sunken"
        >
          <div className="h-full bg-black" style={{ width: `${Math.min(progress.percent, 100)}%` }} />
        </div>
        <PanelRow name="Target" value={`${formatShillings(goal.target)}, ${progress.percent}% there`} />
        <PanelRow name="Interest after tax" value={formatShillings(progress.interest)} />
        <PanelRow name="Awaiting payment" value={formatShillings(progress.awaitingPayment)} />
        <PanelRow name="Needed by" value={goal.targetDate ? formatDay(goal.targetDate) : "No date"} />
      </Panel>
      <Panel title="Investments in this goal">
        {savings.length === 0 ? (
          <p className="m-0 text-md text-muted">No saving has been made towards this goal.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col p-0">
            {savings.map((saving) => (
              <li key={saving.investmentId} className="flex justify-between gap-3 border-t border-divider py-2.5 text-md">
                <div className="min-w-0">
                  <div className="font-bold">{saving.productName}</div>
                  <div className="text-xs text-muted">
                    {saving.startDate && saving.maturityDate
                      ? `${formatDay(saving.startDate)} to ${formatDay(saving.maturityDate)}`
                      : "Not started"}
                    {saving.rolledFrom ? ", rolled over from the last term" : ""}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className="font-bold whitespace-nowrap tabular-nums">{formatShillings(saving.amount)}</span>
                  <Pill tone={SAVING_TONES[saving.status]}>{SAVING_LABELS[saving.status]}</Pill>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </section>
  );
}

export default function GoalsPage() {
  return <GoalsScreen />;
}
