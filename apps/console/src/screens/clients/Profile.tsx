import { useEffect, useState } from "react";
import { formatTime } from "./labels.ts";
import { ANSWER_LABELS, PROFILE_LABELS, RELATIONSHIP_LABELS, type ClientProfile, type ProfileApi } from "./profile.ts";

type Load = { state: "loading" } | { state: "failed" } | { state: "ready"; profile: ClientProfile };

/** Next of kin and the investing questions, on the client page. */
export function ProfileSection({ api, clientId }: { api: ProfileApi; clientId: string }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  useEffect(() => {
    let current = true;
    api.profile(clientId).then(
      (profile) => current && setLoad({ state: "ready", profile }),
      () => current && setLoad({ state: "failed" }),
    );
    return () => {
      current = false;
    };
  }, [api, clientId]);

  if (load.state === "loading") return null;
  if (load.state === "failed") {
    return (
      <p role="alert" className="m-0 max-w-[720px] rounded-md bg-surface-sunken px-3 py-2.5 text-sm text-muted">
        The client's next of kin and investing answers could not be loaded.
      </p>
    );
  }
  const { profile } = load;
  const [latest] = profile.risk;
  return (
    <div className="grid max-w-[1100px] gap-4 [grid-template-columns:repeat(auto-fit,minmax(320px,1fr))]">
      <section aria-label="Next of kin" className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-surface p-4">
        <h3 className="m-0 text-[17px] font-extrabold">Next of kin</h3>
        {profile.nextOfKin === null ? (
          <p className="m-0 text-sm text-muted">Sealed details cannot be opened here.</p>
        ) : profile.nextOfKin.length === 0 ? (
          <p className="m-0 text-sm text-muted">The client has not named anyone.</p>
        ) : (
          <>
            <ul className="m-0 flex list-none flex-col p-0">
              {profile.nextOfKin.map((person) => (
                <li key={`${person.fullName}-${person.phone}`} className="flex items-start justify-between gap-3 border-b border-border py-2.5 text-sm last:border-b-0">
                  <div className="flex min-w-0 flex-col">
                    <span className="font-bold">{person.fullName}</span>
                    <span className="text-muted">
                      {RELATIONSHIP_LABELS[person.relationship]} · {person.phone} · ID {person.idNumberMasked}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            {profile.nextOfKinUpdatedAt ? <p className="m-0 text-xs text-muted">Changed {formatTime(profile.nextOfKinUpdatedAt)}. Opening this is recorded in the audit trail.</p> : null}
          </>
        )}
      </section>
      <section aria-label="Investing questions" className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-surface p-4">
        <h3 className="m-0 text-[17px] font-extrabold">Investing questions</h3>
        {latest ? (
          <>
            <p className="m-0 text-sm">
              <span className="rounded-pill bg-success-surface px-2.5 py-0.5 text-xs font-bold text-success">{PROFILE_LABELS[latest.profile].label}</span>{" "}
              {PROFILE_LABELS[latest.profile].detail}
            </p>
            <dl className="m-0 grid gap-x-4 gap-y-2 text-sm [grid-template-columns:max-content_minmax(0,1fr)]">
              {Object.entries(ANSWER_LABELS).map(([code, words]) => (
                <div key={code} className="contents">
                  <dt className="font-bold text-muted">{words.question}</dt>
                  <dd className="m-0">{words.options[latest.answers[code] ?? ""] ?? "Not answered"}</dd>
                </div>
              ))}
            </dl>
            <p className="m-0 text-xs text-muted">
              Answered {formatTime(latest.answeredAt)}, asked again from {formatTime(latest.dueAgainAt)}.
              {profile.risk.length > 1 ? ` Answered ${profile.risk.length} times in all.` : ""}
            </p>
          </>
        ) : (
          <p className="m-0 text-sm text-muted">Not answered yet. The answers guide suggestions and never stop a client investing.</p>
        )}
      </section>
    </div>
  );
}
