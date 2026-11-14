import { useCallback, useEffect, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { notConnectedTeamApi, teamErrorMessage, TeamError, type Change, type Decision, type Member, type NewChange, type Team, type TeamApi } from "./api.ts";
import { createHttpTeamApi } from "./http.ts";
import { ChangeCard, InviteForm, MemberRow, RoleMatrix } from "./Parts.tsx";
import { createStubTeamApi } from "./stub.ts";

const SUMMARY = "Who works here, what each role can do, and what is waiting for a second person.";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; team: Team };

interface TeamScreenProps {
  api: TeamApi;
  /** The signed-in member of staff. */
  me: string;
}

export function TeamScreen({ api, me }: TeamScreenProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  // Which form or card the last error belongs to: a change ID, a member ID, or "invite".
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [inviting, setInviting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchTeam = useCallback(
    (): Promise<Load> =>
      api.load().then(
        (team) => ({ state: "ready", team }),
        (error: unknown) => ({ state: "failed", message: teamErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchTeam().then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetchTeam]);

  async function run<T>(on: string, command: () => Promise<T>, done: (result: T) => string): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    setNotice(null);
    try {
      const change = await command();
      const fresh = await fetchTeam();
      if (fresh.state === "ready") setLoad(fresh);
      setNotice(done(change));
      return true;
    } catch (error) {
      setFailed({ on, message: teamErrorMessage(error) });
      if (error instanceof TeamError && (error.code === "not_found" || error.code === "waiting")) {
        const fresh = await fetchTeam();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  const propose = (on: string) => (change: NewChange) =>
    run(on, () => api.propose(change), () => "Sent. Another admin must approve it before anything changes.").then((done) => {
      if (done && on === "invite") setInviting(false);
      return done;
    });

  const syncAccess = (member: Member) => () =>
    run(member.id, () => api.syncAccess(member.id), () => `Asked again. Cloudflare Access for ${member.name} updates in a minute or two.`);

  const decide = (change: Change) => (decision: Decision, reason: string) =>
    run(change.id, () => api.decide(change.id, decision, reason), () =>
      decision === "approved" ? "Approved. The change is in effect." : decision === "rejected" ? "Rejected. Nothing changed." : "Withdrawn.",
    );

  if (load.state === "loading") {
    return (
      <>
        <PageHeader title="Team and permissions" summary={SUMMARY} />
        <p role="status" className="m-0 text-md text-muted">
          Loading the team
        </p>
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        <PageHeader title="Team and permissions" summary={SUMMARY} />
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">The team could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchTeam().then(setLoad);
            }}
          >
            Try again
          </Button>
        </section>
      </>
    );
  }

  const { team } = load;
  const waitingFor = new Set(team.changes.map((change) => change.staffId));
  const active = team.members.filter((member) => member.status !== "disabled");
  const left = team.members.filter((member) => member.status === "disabled");

  return (
    <>
      <PageHeader title="Team and permissions" summary={SUMMARY} />
      {notice ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {notice}
        </p>
      ) : null}

      <section aria-labelledby="waiting" className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h2 id="waiting" className="m-0 text-xl font-extrabold">
            Waiting for a second admin
          </h2>
          <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
            Every change to the team needs two admins: one proposes it with a reason, a different one approves it. Nobody changes their own account.
          </p>
        </div>
        {team.changes.length === 0 ? (
          <p className="m-0 max-w-[640px] rounded-xl border border-border bg-surface p-4 text-md text-muted">Nothing is waiting.</p>
        ) : (
          <div className="grid max-w-[1100px] gap-3 [grid-template-columns:repeat(auto-fill,minmax(300px,1fr))]">
            {team.changes.map((change) => (
              <ChangeCard
                key={change.id}
                change={change}
                canChange={team.canChange}
                busy={busy}
                error={failed?.on === change.id ? failed.message : null}
                onDecide={decide(change)}
              />
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="people" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h2 id="people" className="m-0 text-xl font-extrabold">
              People
            </h2>
            <p className="m-0 text-md text-muted">
              {active.length} {active.length === 1 ? "person" : "people"} with access{left.length > 0 ? `, ${left.length} deactivated` : ""}.
            </p>
          </div>
          {team.canChange && !inviting ? <Button onClick={() => setInviting(true)}>Invite someone</Button> : null}
        </div>
        {inviting ? (
          <InviteForm busy={busy} error={failed?.on === "invite" ? failed.message : null} onPropose={propose("invite")} onCancel={() => setInviting(false)} />
        ) : null}
        {!team.canChange ? (
          <p className="m-0 max-w-[720px] rounded-md bg-surface-sunken px-3 py-2.5 text-sm text-muted">Your role can see the team. Changing it needs an admin.</p>
        ) : null}
        <ul aria-label="People" className="m-0 list-none rounded-xl border border-border bg-surface px-4 py-1">
          {[...active, ...left].map((member) => (
            <MemberRow
              key={member.id}
              member={member}
              isMe={member.id === me}
              canChange={team.canChange}
              waiting={waitingFor.has(member.id)}
              busy={busy}
              error={failed?.on === member.id ? failed.message : null}
              onPropose={propose(member.id)}
              onSyncAccess={syncAccess(member)}
            />
          ))}
        </ul>
      </section>

      <section aria-labelledby="roles" className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h2 id="roles" className="m-0 text-xl font-extrabold">
            What each role can do
          </h2>
          <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
            The menu shows each person only what their role opens. The Finvest service checks every request again, so this table is the rule, not a suggestion.
          </p>
        </div>
        <RoleMatrix roles={team.roles} />
      </section>
    </>
  );
}

export default function TeamPage() {
  const { session } = useAuth();
  const [api] = useState<TeamApi>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=quiet` shows nothing waiting, `?sample=access` shows Cloudflare Access problems, and `?sample=offline` as if Finvest could not be reached.
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return notConnectedTeamApi;
      return createStubTeamApi({ id: session.staffId, name: session.name }, Date.now, sample === "quiet" || sample === "access" ? sample : "sample");
    }
    const base = apiBaseUrl();
    return base ? createHttpTeamApi(createHttp(base)) : notConnectedTeamApi;
  });
  return <TeamScreen api={api} me={session?.staffId ?? ""} />;
}
