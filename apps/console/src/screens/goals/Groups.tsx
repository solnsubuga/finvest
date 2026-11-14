import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { Panel, PanelRow, Pill } from "@/screens/migration/Checks.tsx";
import type { Currency, Standing } from "./api.ts";
import { groupsErrorMessage, type Group, type GroupDetail, type GroupsApi, type GroupStatus } from "./groupsApi.ts";
import { CATEGORY_LABELS, formatDay, formatMoney, STANDING_LABELS } from "./labels.ts";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };
type Filter = GroupStatus | "all";

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

/** Group goals: a shared target, with each member saving into their own goal. Only reads. */
export function GroupGoals({ api }: { api: GroupsApi }) {
  const [load, setLoad] = useState<Load<readonly Group[]>>({ state: "loading" });
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<string | null>(null);

  const fetchGroups = useCallback(
    (): Promise<Load<readonly Group[]>> =>
      api.listGroups(null).then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: groupsErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchGroups().then((result) => current && setLoad(result));
    return () => {
      current = false;
    };
  }, [fetchGroups]);

  if (load.state === "loading") {
    return (
      <p role="status" className="m-0 text-md text-muted">
        Loading group goals
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
        <h2 className="m-0 text-xl font-extrabold">Group goals could not be loaded</h2>
        <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
        <Button
          variant="secondary"
          onClick={() => {
            setLoad({ state: "loading" });
            void fetchGroups().then(setLoad);
          }}
        >
          Try again
        </Button>
      </section>
    );
  }

  const groups = load.value;
  const open = groups.filter((group) => group.status === "active");
  const shown = filter === "all" ? groups : groups.filter((group) => group.status === filter);

  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
        <Card title="Open group goals" value={String(open.length)} note={`${groups.length - open.length} closed`} />
        <Card title="With a date" value={String(open.filter((group) => group.targetDate !== null).length)} note="Among the open ones" />
      </div>
      <div className="flex flex-wrap items-start gap-5">
        <section aria-label="Group goals" className="flex min-w-0 flex-[3_1_560px] flex-col gap-3">
          <div role="group" aria-label="Show group goals" className="flex flex-wrap gap-2">
            {FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={filter === option.value}
                onClick={() => setFilter(option.value)}
                className={cn(
                  "h-9 rounded-pill border px-3.5 text-sm font-bold",
                  filter === option.value ? "border-black bg-black text-white" : "border-border-strong bg-surface text-ink",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          {groups.length === 0 ? (
            <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
              <h2 className="m-0 text-xl font-extrabold">No group goals yet</h2>
              <p className="m-0 text-md leading-normal text-muted">
                A group goal appears here when a client sets one up in the app and invites family or friends. Each member saves into their
                own goal.
              </p>
            </div>
          ) : shown.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
              No group goal is {filter === "active" ? "open" : "closed"}.
            </p>
          ) : (
            <GroupsTable groups={shown} selected={selected} onSelect={setSelected} />
          )}
        </section>
        <aside className="flex min-w-0 flex-[2_1_320px] flex-col gap-4">
          {selected ? (
            <Detail key={selected} api={api} groupId={selected} />
          ) : (
            <Panel title="How a group goal works">
              <PanelRow name="Money held" value="None: a shared target" />
              <PanelRow name="Each member" value="Saves into their own goal" />
              <PanelRow name="Members see" value="Names and the total, not amounts" />
              <PanelRow name="Leaving" value="Any time, keeping their goal" />
              <p className="m-0 text-sm leading-normal text-muted">
                This screen only reads. Choose a group goal to see its members and what each one&apos;s own goal was worth when last counted. Each member&apos;s own goal is also on the Goals view.
                Only the client who set it up invites, removes members or closes it, and none of that moves money.
              </p>
            </Panel>
          )}
        </aside>
      </div>
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

function GroupsTable({ groups, selected, onSelect }: { groups: readonly Group[]; selected: string | null; onSelect: (groupId: string) => void }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[640px] border-collapse text-md">
        <caption className="sr-only">Group goals</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Group goal", "Target together", "Status"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr key={group.id} className={cn("border-t border-divider align-top", selected === group.id && "bg-surface-sunken")}>
              <th scope="row" className="px-3 py-3 text-left">
                <button
                  type="button"
                  onClick={() => onSelect(group.id)}
                  aria-pressed={selected === group.id}
                  className="text-left font-bold underline-offset-2 hover:underline"
                >
                  {group.name}
                </button>
                <div className="text-xs font-normal text-muted">
                  {CATEGORY_LABELS[group.category]}, set up {formatDay(group.createdOn)}
                </div>
              </th>
              <td className="px-3 py-3 whitespace-nowrap">
                <div className="font-bold tabular-nums">{formatMoney(group.target, group.currency)}</div>
                <div className="text-xs text-muted">{group.targetDate ? `By ${formatDay(group.targetDate)}` : "No date"}</div>
              </td>
              <td className="px-3 py-3">
                {group.status === "active" ? (
                  <Pill tone="bg-success-surface text-success">Open</Pill>
                ) : (
                  <>
                    <Pill tone="bg-surface-sunken text-muted">Closed</Pill>
                    {group.closedOn ? <div className="mt-1.5 text-xs text-muted">On {formatDay(group.closedOn)}</div> : null}
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

function Detail({ api, groupId }: { api: GroupsApi; groupId: string }) {
  const [load, setLoad] = useState<Load<GroupDetail>>({ state: "loading" });

  useEffect(() => {
    let current = true;
    void api.getGroup(groupId).then(
      (value) => current && setLoad({ state: "ready", value }),
      (error: unknown) => current && setLoad({ state: "failed", message: groupsErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [api, groupId]);

  if (load.state === "loading") {
    return (
      <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
        Loading the group goal
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
  const { group, progress, members } = load.value;
  const current = members.filter((member) => member.leftOn === null);
  const past = members.filter((member) => member.leftOn !== null);
  const counted = current.map((member) => member.valueOn).filter((day): day is string => day !== null);
  const asOf = counted.length > 0 ? counted.reduce((a, b) => (a < b ? a : b)) : null;
  return (
    <section aria-label={`Group goal: ${group.name}`} className="flex flex-col gap-4">
      <Panel title={group.name}>
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-2xl font-extrabold tabular-nums">{formatMoney(progress.value, group.currency)}</span>
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
        <PanelRow name="Target together" value={`${formatMoney(group.target, group.currency)}, ${progress.percent}% there`} />
        <PanelRow name="Needed by" value={group.targetDate ? formatDay(group.targetDate) : "No date"} />
        <PanelRow name="Members now" value={String(current.length)} />
        <PanelRow name="Counted as of" value={asOf ? formatDay(asOf) : "Not counted yet"} />
        <p className="m-0 text-sm leading-normal text-muted">
          Figures are after tax, as the worker counted each member&apos;s own goal in the early morning. Members never see each
          other&apos;s amounts.
        </p>
      </Panel>
      <Panel title="Members">
        <MemberList members={current} ownerClientId={group.ownerClientId} currency={group.currency} />
      </Panel>
      {past.length > 0 ? (
        <Panel title="Left the group">
          <MemberList members={past} ownerClientId={group.ownerClientId} currency={group.currency} />
        </Panel>
      ) : null}
    </section>
  );
}

function MemberList({ members, ownerClientId, currency }: { members: GroupDetail["members"]; ownerClientId: string; currency: Currency }) {
  return (
    <ul className="m-0 flex list-none flex-col p-0">
      {members.map((member) => (
        <li key={member.goalId} className="flex justify-between gap-3 border-t border-divider py-2.5 text-md">
          <div className="min-w-0">
            <div className="font-bold">
              {member.displayName}
              {member.clientId === ownerClientId ? <span className="font-normal text-muted">, set it up</span> : null}
            </div>
            <div className="text-xs text-muted">
              Joined {formatDay(member.joinedOn)}
              {member.leftOn ? `, left ${formatDay(member.leftOn)}` : ""}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1 text-right">
            <span className="font-bold whitespace-nowrap tabular-nums">{formatMoney(member.value, currency)}</span>
            <span className="text-xs text-muted">{member.valueOn ? `On ${formatDay(member.valueOn)}` : "Not counted yet"}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
