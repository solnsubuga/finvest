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
import { HeldSection } from "./Held.tsx";
import { createHttpReviewQueueApi } from "./http.ts";
import { createStubReviewQueueApi, type StubCase } from "./stub.ts";

type Tab = "accounts" | "held";

const SUMMARY = "Only what the system could not clear on its own: payout accounts to check, and large withdrawals to approve.";

export function ReviewQueueScreen({
  api,
  directory,
  me,
  canDecide,
  now = () => new Date(),
  newKey = () => crypto.randomUUID(),
}: {
  api: ReviewQueueApi;
  directory: Directory;
  me: string;
  /** Whether this person holds `payout_accounts.change`. */
  canDecide: boolean;
  now?: () => Date;
  newKey?: () => string;
}) {
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "held" ? "held" : "accounts";
  const [counts, setCounts] = useState<Partial<Record<Tab, number>>>({});
  const countAccounts = useCallback((count: number) => setCounts((known) => ({ ...known, accounts: count })), []);
  const countHeld = useCallback((count: number) => setCounts((known) => ({ ...known, held: count })), []);

  const tabs: { id: Tab; label: string }[] = [
    { id: "accounts", label: "Payout accounts" },
    { id: "held", label: "Held payouts" },
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
                if (id !== tab) setParams(id === "held" ? { tab: "held" } : {});
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

      {tab === "accounts" ? (
        <AccountsSection
          api={api}
          directory={directory}
          me={me}
          canDecide={canDecide}
          now={now}
          selectedId={params.get("account")}
          onSelect={(id) => setParams({ account: id })}
          onCount={countAccounts}
        />
      ) : (
        <HeldSection
          api={api}
          directory={directory}
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
  const [{ api, directory }] = useState<{ api: ReviewQueueApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      const me = { id: session.staffId, name: session.name };
      return { api: createStubReviewQueueApi(me.id, Date.now, stubCase()), directory: createStubDirectory(me) };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedReviewQueueApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpReviewQueueApi(http), directory: createHttpDirectory(http) };
  });
  return (
    <ReviewQueueScreen api={api} directory={directory} me={session?.staffId ?? ""} canDecide={session ? can(session, "payout_accounts.change") : false} />
  );
}
