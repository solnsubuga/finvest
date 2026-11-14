import { useCallback, useState } from "react";
import { useSearchParams } from "react-router";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { AccountsSection } from "./Accounts.tsx";
import { notConnectedReviewQueueApi, type ReviewQueueApi } from "./api.ts";
import { EverythingSection } from "./Everything.tsx";
import {
  createHttpWholeQueueApi,
  createStubWholeQueueApi,
  notConnectedWholeQueueApi,
  type QueueGroup,
  type WholeQueueApi,
} from "./everything.ts";
import { HeldSection } from "./Held.tsx";
import { createHttpReviewQueueApi } from "./http.ts";
import {
  createHttpOrganisationsApi,
  createStubOrganisationsApi,
  notConnectedOrganisationsApi,
  type OrganisationsApi,
} from "./organisations.ts";
import { OrganisationsSection } from "./Organisations.tsx";
import { createStubReviewQueueApi, type StubCase } from "./stub.ts";
import { createHttpTransfersApi, createStubTransfersApi, notConnectedTransfersApi, type TransfersApi } from "./transfers.ts";
import { TransfersSection } from "./Transfers.tsx";

type Tab = "all" | "accounts" | "held" | "transfers" | "organisations";

const GROUPS: readonly QueueGroup[] = ["deposits", "identity", "accounts", "withdrawals", "transfers"];

const SUMMARY = "Only what the system could not clear on its own, longest waiting first.";

function tabOf(value: string | null, canReviewOrganisations: boolean): Tab {
  if (value === "accounts" || value === "held" || value === "transfers") return value;
  return value === "organisations" && canReviewOrganisations ? "organisations" : "all";
}

export function ReviewQueueScreen({
  api,
  whole = notConnectedWholeQueueApi,
  transfers = notConnectedTransfersApi,
  directory,
  me,
  canDecide,
  canDecideTransfers = false,
  organisations = notConnectedOrganisationsApi,
  canReviewOrganisations = false,
  now = () => new Date(),
  newKey = () => crypto.randomUUID(),
}: {
  api: ReviewQueueApi;
  /** Everything waiting, in one list. */
  whole?: WholeQueueApi;
  transfers?: TransfersApi;
  /** Company and group account applications, shown only to someone with `organisations.review`. */
  organisations?: OrganisationsApi;
  canReviewOrganisations?: boolean;
  directory: Directory;
  me: string;
  /** Whether this person holds `payout_accounts.change`. */
  canDecide: boolean;
  /** Whether this person holds `investment_transfers.change`. */
  canDecideTransfers?: boolean;
  now?: () => Date;
  newKey?: () => string;
}) {
  const [params, setParams] = useSearchParams();
  const tab = tabOf(params.get("tab"), canReviewOrganisations);
  const shownGroup = params.get("group");
  const group = GROUPS.find((g) => g === shownGroup) ?? null;
  const [counts, setCounts] = useState<Partial<Record<Tab, number>>>({});
  const countAccounts = useCallback((count: number) => setCounts((known) => ({ ...known, accounts: count })), []);
  const countHeld = useCallback((count: number) => setCounts((known) => ({ ...known, held: count })), []);
  const countTransfers = useCallback((count: number) => setCounts((known) => ({ ...known, transfers: count })), []);
  const countOrganisations = useCallback((count: number) => setCounts((known) => ({ ...known, organisations: count })), []);
  const countAll = useCallback((count: number) => setCounts((known) => ({ ...known, all: count })), []);

  const tabs: { id: Tab; label: string }[] = [
    { id: "all", label: "Everything waiting" },
    { id: "accounts", label: "Payout accounts" },
    { id: "held", label: "Held payouts" },
    { id: "transfers", label: "Held transfers" },
    ...(canReviewOrganisations ? [{ id: "organisations" as const, label: "Company accounts" }] : []),
  ];

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Review queue" summary={SUMMARY} />
        <div role="tablist" aria-label="Review queue" className="flex flex-wrap gap-2">
          {tabs.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => {
                if (id !== tab) setParams(id === "all" ? {} : { tab: id });
              }}
              className={cn(
                "h-11 cursor-pointer rounded-pill px-4 text-sm",
                tab === id ? "bg-black font-bold text-white" : "border border-border-strong bg-surface font-semibold text-ink",
              )}
            >
              {label}
              {counts[id] !== undefined ? ` (${counts[id]})` : ""}
            </button>
          ))}
        </div>
      </div>

      {tab === "all" ? (
        <EverythingSection
          api={whole}
          directory={directory}
          now={now}
          group={group}
          onGroup={(chosen) => setParams(chosen ? { group: chosen } : {})}
          onCount={countAll}
        />
      ) : tab === "accounts" ? (
        <AccountsSection
          api={api}
          directory={directory}
          me={me}
          canDecide={canDecide}
          now={now}
          selectedId={params.get("account")}
          onSelect={(id) => setParams({ tab: "accounts", account: id })}
          onCount={countAccounts}
        />
      ) : tab === "transfers" ? (
        <TransfersSection
          api={transfers}
          directory={directory}
          me={me}
          canDecide={canDecideTransfers}
          now={now}
          newKey={newKey}
          selectedId={params.get("transfer")}
          onSelect={(id) => setParams({ tab: "transfers", transfer: id })}
          onCount={countTransfers}
        />
      ) : tab === "organisations" ? (
        <OrganisationsSection
          api={organisations}
          now={now}
          selectedId={params.get("organisation")}
          onSelect={(id) => setParams({ tab: "organisations", organisation: id })}
          onCount={countOrganisations}
        />
      ) : (
        <HeldSection
          api={api}
          directory={directory}
          me={me}
          canDecide={canDecide}
          now={now}
          newKey={newKey}
          selectedId={params.get("payout")}
          onSelect={(id) => setParams({ tab: "held", payout: id })}
          onCount={countHeld}
        />
      )}
    </>
  );
}

function stubCase(): StubCase {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" ? sample : "sample";
}

export default function ReviewQueuePage() {
  const { session } = useAuth();
  // A sample queue under `pnpm dev` or a preview build, like sign-in. `?sample=empty` and
  // `?sample=error` show those states. Otherwise the real API.
  const [{ api, whole, transfers, directory, organisations }] = useState<{
    api: ReviewQueueApi;
    whole: WholeQueueApi;
    transfers: TransfersApi;
    directory: Directory;
    organisations: OrganisationsApi;
  }>(() => {
    if (isStubAuth() && session) {
      const me = { id: session.staffId, name: session.name };
      return {
        api: createStubReviewQueueApi(me.id, Date.now, stubCase()),
        whole: createStubWholeQueueApi(me.id, Date.now, stubCase()),
        transfers: createStubTransfersApi(me.id, Date.now, stubCase()),
        directory: createStubDirectory(me),
        organisations: createStubOrganisationsApi(me.id, Date.now, stubCase()),
      };
    }
    const base = apiBaseUrl();
    if (!base) {
      return { api: notConnectedReviewQueueApi, whole: notConnectedWholeQueueApi, transfers: notConnectedTransfersApi, directory: emptyDirectory, organisations: notConnectedOrganisationsApi };
    }
    const http = createHttp(base);
    return {
      api: createHttpReviewQueueApi(http),
      whole: createHttpWholeQueueApi(http),
      transfers: createHttpTransfersApi(http),
      directory: createHttpDirectory(http),
      organisations: createHttpOrganisationsApi(http),
    };
  });
  return (
    <ReviewQueueScreen
      api={api}
      whole={whole}
      transfers={transfers}
      directory={directory}
      organisations={organisations}
      canReviewOrganisations={session ? can(session, "organisations.review") : false}
      me={session?.staffId ?? ""}
      canDecide={session ? can(session, "payout_accounts.change") : false}
      canDecideTransfers={session ? can(session, "investment_transfers.change") : false}
    />
  );
}
