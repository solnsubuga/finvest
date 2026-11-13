import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  notConnectedWordingsApi,
  WordingsError,
  wordingsErrorMessage,
  type Decision,
  type Problem,
  type Wording,
  type WordingText,
  type WordingsApi,
} from "./api.ts";
import { AnnouncementsSection } from "./Announcements.tsx";
import { notConnectedAnnouncementsApi, type AnnouncementsApi } from "./announcements.ts";
import { createStubAnnouncementsApi } from "./announcementsStub.ts";
import { CATEGORIES, CATEGORY_LABELS } from "./labels.ts";
import { createStubWordingsApi } from "./stub.ts";
import { WordingPanel, WordingRow } from "./Wordings.tsx";

const SUMMARY = "The rules the platform runs on. Changes here need a second person.";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; wordings: Wording[] };

interface SettingsScreenProps {
  api: WordingsApi;
  announcements: AnnouncementsApi;
  directory: Directory;
  /** `wordings.change`: propose and decide changes. The API checks it again. */
  canChange: boolean;
}

export function SettingsScreen({ api, announcements, directory, canChange }: SettingsScreenProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ on: string; message: string; problems: readonly Problem[] } | null>(null);
  const [names, setNames] = useState<Record<string, string | null>>({});
  // Bumped after each change, so the panel's forms start again from the words now in use.
  const [round, setRound] = useState(0);
  const asked = useRef(new Set<string>());

  const lookUp = useCallback(
    (wordings: readonly Wording[]) => {
      for (const staffId of wordings.flatMap((wording) => (wording.proposal ? [wording.proposal.proposedBy] : []))) {
        if (asked.current.has(staffId)) continue;
        asked.current.add(staffId);
        void directory.staffName(staffId).then(
          (name) => setNames((known) => ({ ...known, [staffId]: name })),
          () => setNames((known) => ({ ...known, [staffId]: null })),
        );
      }
    },
    [directory],
  );

  const fetchList = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (wordings) => {
          lookUp(wordings);
          return { state: "ready", wordings };
        },
        (error: unknown) => ({ state: "failed", message: wordingsErrorMessage(error) }),
      ),
    [api, lookUp],
  );

  useEffect(() => {
    let current = true;
    void fetchList().then((next) => {
      if (!current) return;
      setLoad(next);
      // A change waiting for a decision comes first, as it is the work to do.
      if (next.state === "ready") {
        setSelected((next.wordings.find((wording) => wording.proposal) ?? next.wordings[0])?.event ?? null);
      }
    });
    return () => {
      current = false;
    };
  }, [fetchList]);

  async function run(event: string, command: () => Promise<Wording>): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    try {
      const updated = await command();
      lookUp([updated]);
      setLoad((last) =>
        last.state === "ready"
          ? { state: "ready", wordings: last.wordings.map((wording) => (wording.event === event ? updated : wording)) }
          : last,
      );
      setRound((count) => count + 1);
      return true;
    } catch (error) {
      setFailed({ on: event, message: wordingsErrorMessage(error), problems: error instanceof WordingsError ? error.problems : [] });
      if (error instanceof WordingsError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchList();
        if (fresh.state === "ready") setLoad(fresh);
        setRound((count) => count + 1);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (load.state === "loading") {
    return (
      <>
        <PageHeader title="Settings" summary={SUMMARY} />
        <p role="status" className="m-0 text-md text-muted">
          Loading settings
        </p>
        <AnnouncementsSection api={announcements} directory={directory} canChange={canChange} />
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        <PageHeader title="Settings" summary={SUMMARY} />
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Client messages could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchList().then(setLoad);
            }}
          >
            Try again
          </Button>
        </section>
        <AnnouncementsSection api={announcements} directory={directory} canChange={canChange} />
      </>
    );
  }

  const { wordings } = load;
  const current = wordings.find((wording) => wording.event === selected);
  const waiting = wordings.filter((wording) => wording.proposal).length;
  const staffName = (staffId: string) => names[staffId] ?? "a member of staff";

  return (
    <>
      <PageHeader title="Settings" summary={SUMMARY} />
      <section aria-labelledby="client-messages" className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 id="client-messages" className="m-0 text-xl font-extrabold">
            Client messages
          </h2>
          <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
            The words of every notice Finvest sends a client, in the app, by push, SMS and email.{" "}
            {waiting === 0 ? "No change is waiting." : waiting === 1 ? "1 change is waiting for a second person." : `${waiting} changes are waiting for a second person.`}{" "}
            Sign-in codes and set-up links are fixed and are not listed.
          </p>
        </div>
        {wordings.length === 0 ? (
          <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
            <h3 className="m-0 text-[17px] font-extrabold">No messages to change</h3>
            <p className="m-0 text-md leading-normal text-muted">Finvest has not listed any client messages yet.</p>
          </div>
        ) : (
          <div className="flex flex-wrap items-start gap-5">
            <nav aria-label="Messages" className="flex min-w-0 flex-[1_1_280px] flex-col gap-4">
              {CATEGORIES.map((category) => {
                const inCategory = wordings.filter((wording) => wording.category === category);
                if (inCategory.length === 0) return null;
                return (
                  <div key={category} className="flex flex-col gap-2">
                    <h3 className="m-0 text-xs font-extrabold tracking-[0.6px] text-muted uppercase">{CATEGORY_LABELS[category]}</h3>
                    {inCategory.map((wording) => (
                      <WordingRow
                        key={wording.event}
                        wording={wording}
                        selected={wording.event === selected}
                        onSelect={() => {
                          setSelected(wording.event);
                          setFailed(null);
                        }}
                      />
                    ))}
                  </div>
                );
              })}
            </nav>
            <div className="min-w-0 flex-[2_1_420px]">
              {current ? (
                <WordingPanel
                  key={`${current.event}:${round}`}
                  wording={current}
                  canChange={canChange}
                  staffName={staffName}
                  busy={busy}
                  error={failed?.on === current.event ? failed.message : null}
                  refused={failed?.on === current.event ? failed.problems : []}
                  onPropose={(text: WordingText, reason: string) => run(current.event, () => api.propose(current.event, text, reason))}
                  onDecide={(decision: Decision, reason: string) =>
                    current.proposal ? run(current.event, () => api.decide(current.proposal?.id ?? "", decision, reason)) : Promise.resolve(false)
                  }
                />
              ) : null}
            </div>
          </div>
        )}
      </section>
      <AnnouncementsSection api={announcements} directory={directory} canChange={canChange} />
    </>
  );
}

export default function SettingsPage() {
  const { session } = useAuth();
  // Sample messages under `pnpm dev` or a preview build, like sign-in. Otherwise nothing until the API client exists.
  const [{ api, announcements, directory }] = useState<{ api: WordingsApi; announcements: AnnouncementsApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      const me = { id: session.staffId, name: session.name };
      // Under the stub, `?sample=offline` shows the screen as if Finvest could not be reached, and `?sample=empty` with no announcements.
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") {
        return { api: notConnectedWordingsApi, announcements: notConnectedAnnouncementsApi, directory: createStubDirectory(me) };
      }
      return {
        api: createStubWordingsApi(me),
        announcements: createStubAnnouncementsApi(me, Date.now, sample === "empty" ? "empty" : "sample"),
        directory: createStubDirectory(me),
      };
    }
    return { api: notConnectedWordingsApi, announcements: notConnectedAnnouncementsApi, directory: emptyDirectory };
  });
  return (
    <SettingsScreen
      api={api}
      announcements={announcements}
      directory={directory}
      canChange={session ? can(session, "wordings.change") : false}
    />
  );
}
