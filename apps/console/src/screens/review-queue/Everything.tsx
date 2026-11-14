import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import type { Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { wholeQueueMessage, type QueueGroup, type QueueItem, type QueueKind, type WholeQueue, type WholeQueueApi } from "./everything.ts";
import { formatAmount, timeAgo } from "./labels.ts";
import { useNames } from "./names.ts";
import { Empty, Failure, Loading, Pill, type Load } from "./Parts.tsx";

export const GROUP_LABELS: Record<QueueGroup, string> = {
  deposits: "Deposits",
  identity: "Identity checks",
  accounts: "Accounts",
  withdrawals: "Withdrawals",
  transfers: "Transfers",
};

const GROUPS: QueueGroup[] = ["deposits", "identity", "accounts", "withdrawals", "transfers"];

const GROUP_OF: Record<QueueKind, QueueGroup> = {
  bank_transfer: "deposits",
  identity_check: "identity",
  payout_account: "accounts",
  organisation: "accounts",
  held_payout: "withdrawals",
  payout_held_up: "withdrawals",
  payout_unanswered: "withdrawals",
  investment_transfer: "transfers",
};

export const KIND_TITLES: Record<QueueKind, string> = {
  bank_transfer: "Bank transfer to match",
  identity_check: "Identity check",
  payout_account: "Payout account to check",
  held_payout: "Large withdrawal held",
  payout_held_up: "Payout not moving",
  payout_unanswered: "Payout with no answer",
  investment_transfer: "Investment transfer held",
  organisation: "Company or group application",
};

const REASONS: Record<string, string> = {
  find_on_statement: "Find it on the trust account statement",
  match_to_approve: "A match waits for a second person",
  needs_review: "Needs a person to look",
  with_compliance: "With Compliance",
  bank_statement: "Proof is a bank statement",
  bank_letter: "Proof is a bank letter",
  bank_card: "Proof is a bank card",
  network_registered_name: "Name from the network",
  above_review_limit: "Above the review limit",
  wallet_short: "Our ioTec wallet is short. Top it up",
  provider_took_nothing: "The provider has taken nothing",
  no_answer: "The provider never answered. Settle it from the statement",
  size: "Large transfer",
  screening: "Held by screening",
  company: "Company",
  savings_group: "Savings group",
  investment_club: "Investment club",
};

/** Where each kind is decided. */
function placeOf(item: QueueItem): string | null {
  switch (item.kind) {
    case "bank_transfer":
    case "payout_unanswered":
      return "/reconciliation";
    case "identity_check":
      return "/identity-checks";
    case "payout_account":
      return `/review-queue?tab=accounts&account=${encodeURIComponent(item.id)}`;
    case "held_payout":
      return `/review-queue?tab=held&payout=${encodeURIComponent(item.id)}`;
    case "payout_held_up":
      return "/money-movement";
    case "investment_transfer":
      return `/review-queue?tab=transfers&transfer=${encodeURIComponent(item.id)}`;
    case "organisation":
      return `/review-queue?tab=organisations&organisation=${encodeURIComponent(item.id)}`;
  }
}

function waited(since: string | null, now: Date): string {
  return since ? timeAgo(since, now).replace(" ago", "") : "Nothing waiting";
}

function Tile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-surface p-4">
      <div className="text-sm text-muted">{label}</div>
      <div className="text-2xl font-extrabold tracking-[-0.4px]">{value}</div>
      <div className="text-xs text-muted">{detail}</div>
    </div>
  );
}

export function EverythingSection({
  api,
  directory,
  now,
  group,
  onGroup,
  onCount,
}: {
  api: WholeQueueApi;
  directory: Directory;
  now: () => Date;
  group: QueueGroup | null;
  onGroup: (group: QueueGroup | null) => void;
  onCount?: (count: number) => void;
}) {
  const [load, setLoad] = useState<Load<WholeQueue>>({ state: "loading" });

  const fetchQueue = useCallback(
    (): Promise<Load<WholeQueue>> =>
      api.load().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: wholeQueueMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchQueue().then((found) => current && setLoad(found));
    return () => {
      current = false;
    };
  }, [fetchQueue]);

  useEffect(() => {
    if (load.state === "ready") onCount?.(load.value.waiting);
  }, [load, onCount]);

  const items = load.state === "ready" ? load.value.items : [];
  const { clients } = useNames(
    directory,
    items.flatMap((item) => (item.clientId ? [item.clientId] : [])),
    [],
  );

  if (load.state === "loading") return <Loading>Loading the queue…</Loading>;
  if (load.state === "failed") {
    return <Failure title="The queue could not be loaded" message={load.message} onRetry={() => void fetchQueue().then(setLoad)} />;
  }

  const queue = load.value;
  const offered = GROUPS.filter((g) => queue.kinds.some((kind) => GROUP_OF[kind] === g));
  const shown = group ? queue.items.filter((item) => item.group === group) : queue.items;
  const count = (g: QueueGroup) => queue.items.filter((item) => item.group === g).length;

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
        <Tile label="Waiting for a person" value={String(queue.waiting)} detail="Across everything you can see" />
        <Tile
          label="Cleared automatically today"
          value={String(queue.clearedAutomaticallyToday)}
          detail="Mobile money deposits and payouts no one had to touch"
        />
        <Tile
          label="Oldest waiting"
          value={waited(queue.oldestWaitingSince, now())}
          detail={queue.oldestWaitingSince ? "Shown first in the list" : "The queue is clear"}
        />
      </div>

      <div role="group" aria-label="Show" className="flex flex-wrap gap-2">
        <FilterButton selected={group === null} onClick={() => onGroup(null)}>
          Everything ({queue.items.length})
        </FilterButton>
        {offered.map((g) => (
          <FilterButton key={g} selected={group === g} onClick={() => onGroup(g)}>
            {GROUP_LABELS[g]} ({count(g)})
          </FilterButton>
        ))}
      </div>

      {shown.length === 0 ? (
        <Empty title={group ? `No ${GROUP_LABELS[group].toLowerCase()} waiting` : "Nothing is waiting"}>
          Everything the system could not clear on its own shows here, longest waiting first. Changes waiting for a second person
          are in the approvals inbox on the Overview.
        </Empty>
      ) : (
        <ul aria-label="Waiting" className="m-0 flex list-none flex-col gap-2 p-0">
          {shown.map((item) => {
            const place = placeOf(item);
            const client = item.clientId ? clients[item.clientId] : null;
            return (
              <li
                key={`${item.kind}:${item.id}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface px-4 py-3.5"
              >
                <div className="flex min-w-0 flex-[1_1_320px] flex-col gap-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-md font-bold">{KIND_TITLES[item.kind]}</span>
                    <Pill tone="quiet">{GROUP_LABELS[item.group]}</Pill>
                    {item.yours ? <Pill tone="waiting">You took the first step</Pill> : null}
                  </div>
                  <span className="text-sm text-muted">
                    {client?.name ?? (item.clientId ? "A client" : "An applicant")}
                    {item.amount ? `, ${formatAmount(item.amount)}` : ""}. {REASONS[item.reason] ?? "Needs a person"}.
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold whitespace-nowrap text-muted">Waiting {waited(item.waitingSince, now())}</span>
                  {place ? (
                    <Link
                      to={place}
                      className="inline-flex h-11 items-center rounded-pill bg-black px-4 text-sm font-bold whitespace-nowrap text-white no-underline"
                    >
                      Open
                    </Link>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function FilterButton({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "h-11 cursor-pointer rounded-pill px-4 text-sm",
        selected ? "bg-black font-bold text-white" : "border border-border-strong bg-surface font-semibold text-ink",
      )}
    >
      {children}
    </button>
  );
}
