import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { approvalsErrorMessage, createHttpApprovalsApi, notConnectedApprovalsApi, type ApprovalItem, type ApprovalsApi } from "./api.ts";
import { describeSubject, kindLabel, linkFor, waitingFor } from "./labels.ts";
import { createStubApprovalsApi } from "./stub.ts";

const SUMMARY = "Client money, payments in progress and what needs attention today.";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: ApprovalItem[] };

function ItemRow({ item, now }: { item: ApprovalItem; now: Date }) {
  const link = linkFor(item);
  const who = item.yours ? "you" : (item.proposedByName ?? "a member of staff");
  return (
    <li className="grid gap-1 border-b border-border px-4 py-3 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:gap-4">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-2xs font-extrabold tracking-[0.6px] text-muted uppercase">{kindLabel(item)}</span>
        <span className="text-md font-bold break-words">{describeSubject(item)}</span>
        <span className="text-sm text-muted">
          Proposed by {who}, <time dateTime={item.proposedAt}>{waitingFor(item.proposedAt, now)}</time>
        </span>
      </div>
      {link ? (
        <Link
          to={link}
          className="justify-self-start rounded-pill border border-border-strong px-4 py-2 text-sm font-bold text-ink no-underline hover:bg-surface-sunken sm:justify-self-end"
        >
          {item.canDecide ? "Review" : "Open"}
          <span className="sr-only">: {kindLabel(item)}, {describeSubject(item)}</span>
        </Link>
      ) : null}
    </li>
  );
}

function Group({ title, hint, items, now, empty }: { title: string; hint: string; items: ApprovalItem[]; now: Date; empty: string }) {
  const id = title.toLowerCase().replaceAll(" ", "-");
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-col gap-0.5">
        <h3 id={id} className="m-0 text-[17px] font-extrabold">
          {title} <span className="font-semibold text-muted">({items.length})</span>
        </h3>
        <p className="m-0 text-sm text-muted">{hint}</p>
      </div>
      {items.length === 0 ? (
        <p className="m-0 rounded-xl border border-border bg-surface p-4 text-sm text-muted">{empty}</p>
      ) : (
        <ul aria-label={title} className="m-0 list-none rounded-xl border border-border bg-surface p-0">
          {items.map((item) => (
            <ItemRow key={`${item.kind ?? "other"}:${item.id}`} item={item} now={now} />
          ))}
        </ul>
      )}
    </section>
  );
}

export function OverviewScreen({ api, now = () => new Date() }: { api: ApprovalsApi; now?: () => Date }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });

  const fetchItems = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (items) => ({ state: "ready", items }),
        (error: unknown) => ({ state: "failed", message: approvalsErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchItems().then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetchItems]);

  return (
    <>
      <PageHeader title="Overview" summary={SUMMARY} />
      <section aria-labelledby="waiting" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h2 id="waiting" className="m-0 text-xl font-extrabold">
              Waiting for a second person
            </h2>
            <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
              Every change that needs two people, from every screen your role can open, oldest first. Each one is approved or rejected on its own screen.
            </p>
          </div>
          {load.state === "ready" ? (
            <Button
              variant="secondary"
              onClick={() => {
                setLoad({ state: "loading" });
                void fetchItems().then(setLoad);
              }}
            >
              Refresh
            </Button>
          ) : null}
        </div>
        {load.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading what is waiting
          </p>
        ) : load.state === "failed" ? (
          <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
            <h3 className="m-0 text-[17px] font-extrabold">What is waiting could not be loaded</h3>
            <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
            <Button
              variant="secondary"
              onClick={() => {
                setLoad({ state: "loading" });
                void fetchItems().then(setLoad);
              }}
            >
              Try again
            </Button>
          </div>
        ) : load.items.length === 0 ? (
          <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
            <h3 className="m-0 text-[17px] font-extrabold">Nothing is waiting</h3>
            <p className="m-0 text-md leading-normal text-muted">When someone proposes a change you can see, it shows here until a second person decides it.</p>
          </div>
        ) : (
          <div className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
            <Group
              title="For you to decide"
              hint="Someone else proposed these, and your role may approve them."
              items={load.items.filter((item) => item.canDecide)}
              now={now()}
              empty="Nothing needs your decision."
            />
            <Group
              title="Yours, waiting for someone else"
              hint="You proposed these. You can withdraw them, never approve them."
              items={load.items.filter((item) => item.yours)}
              now={now()}
              empty="You have nothing waiting."
            />
            {load.items.some((item) => !item.yours && !item.canDecide) ? (
              <Group
                title="Waiting for another role"
                hint="You can see these, but a role with the deciding permission approves them."
                items={load.items.filter((item) => !item.yours && !item.canDecide)}
                now={now()}
                empty=""
              />
            ) : null}
          </div>
        )}
      </section>
    </>
  );
}

export default function OverviewPage() {
  const { session } = useAuth();
  const [api] = useState<ApprovalsApi>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=empty` shows nothing waiting and `?sample=offline` as if Finvest could not be reached.
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return notConnectedApprovalsApi;
      return createStubApprovalsApi({ id: session.staffId, name: session.name, permissions: session.permissions }, Date.now, sample === "empty" ? "empty" : "sample");
    }
    const base = apiBaseUrl();
    return base ? createHttpApprovalsApi(createHttp(base)) : notConnectedApprovalsApi;
  });
  return <OverviewScreen api={api} />;
}
