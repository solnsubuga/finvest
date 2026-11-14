import { ReissuesError, type NewReissue, type Reissue, type ReissueScope, type ReissuesApi } from "./api.ts";

/**
 * Sample note reissues for `pnpm dev` and previews. It keeps the backend's rules that a screen
 * can show: the proposer never decides, at most 31 days and none to come, and nothing to send
 * is refused. The sample counts notes as if each client got one a day and all clients 40.
 */

const OTHER = "sample-staff-grace";
const NONE = { queued: 0, sent: 0, skipped: 0, failed: 0 };

function daysBefore(today: string, days: number): string {
  const date = new Date(`${today}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

function sampleCount(scope: ReissueScope): number {
  const days = daysBetween(scope.issuedFrom, scope.issuedTo) + 1;
  if (scope.clientId === "sample-client-kira") return 0;
  return scope.clientId ? Math.min(days, 3) : days * 40;
}

function samples(me: string, today: string): Reissue[] {
  const base = { decidedBy: null, decidedAt: null, decisionNote: null, finishedAt: null, copies: NONE };
  return [
    {
      ...base,
      id: "-ReissueSample000001",
      clientId: "sample-client-joan",
      issuedFrom: daysBefore(today, 30),
      issuedTo: today,
      reason: "Joan changed her email address and asked in the help chat for her notes again.",
      status: "proposed",
      proposedBy: OTHER,
      proposedAt: `${today}T07:15:00Z`,
      noteCount: 3,
    },
    {
      ...base,
      id: "-ReissueSample000002",
      clientId: null,
      issuedFrom: daysBefore(today, 2),
      issuedTo: daysBefore(today, 2),
      reason: "Resend refused every email for two hours that day. It is fixed.",
      status: "done",
      proposedBy: me,
      proposedAt: `${daysBefore(today, 1)}T06:20:00Z`,
      decidedBy: OTHER,
      decidedAt: `${daysBefore(today, 1)}T06:48:00Z`,
      finishedAt: `${daysBefore(today, 1)}T06:49:00Z`,
      noteCount: 38,
      copies: { queued: 0, sent: 35, skipped: 2, failed: 1 },
    },
  ];
}

export function createStubReissuesApi(
  me: string,
  sample: "default" | "empty" = "default",
  today: () => string = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date()),
  now: () => string = () => new Date().toISOString(),
): ReissuesApi {
  let items = sample === "empty" ? [] : samples(me, today());
  let next = 1;

  function check(scope: ReissueScope): void {
    if (scope.issuedTo > today()) throw new ReissuesError("invalid", [{ field: "issued_to", code: "not_yet" }]);
    if (scope.issuedFrom > scope.issuedTo) throw new ReissuesError("invalid", [{ field: "issued_from", code: "after_issued_to" }]);
    if (daysBetween(scope.issuedFrom, scope.issuedTo) >= 31) throw new ReissuesError("invalid", [{ field: "issued_to", code: "too_long" }]);
  }

  function find(id: string): Reissue {
    const found = items.find((item) => item.id === id);
    if (!found) throw new ReissuesError("not_found");
    if (found.status !== "proposed") throw new ReissuesError("conflict");
    return found;
  }

  function replace(id: string, change: Partial<Reissue>): Reissue {
    items = items.map((item) => (item.id === id ? { ...item, ...change, decidedBy: me, decidedAt: now() } : item));
    return items.find((item) => item.id === id) as Reissue;
  }

  const attempt = <T>(work: () => T): Promise<T> => {
    try {
      return Promise.resolve(work());
    } catch (error) {
      return Promise.reject(error);
    }
  };

  return {
    list: () => Promise.resolve([...items].sort((a, b) => b.proposedAt.localeCompare(a.proposedAt))),
    count: (scope) =>
      attempt(() => {
        check(scope);
        return sampleCount(scope);
      }),
    propose: (_key: string, request: NewReissue) =>
      attempt(() => {
        check(request);
        const noteCount = sampleCount(request);
        if (noteCount === 0) throw new ReissuesError("invalid", [{ field: "issued_from", code: "no_notes" }]);
        const created: Reissue = {
          id: `-ReissueNew${String(next++).padStart(9, "0")}`,
          ...request,
          status: "proposed",
          proposedBy: me,
          proposedAt: now(),
          decidedBy: null,
          decidedAt: null,
          decisionNote: null,
          noteCount,
          copies: NONE,
          finishedAt: null,
        };
        items = [created, ...items];
        return created;
      }),
    approve: (_key, id) =>
      attempt(() => {
        const found = find(id);
        if (found.proposedBy === me) throw new ReissuesError("same_person");
        // The sample sends at once, as the worker does a moment after approval.
        return replace(id, { status: "done", finishedAt: now(), copies: { ...NONE, sent: found.noteCount } });
      }),
    decline: (_key, id, note) =>
      attempt(() => {
        if (find(id).proposedBy === me) throw new ReissuesError("same_person");
        return replace(id, { status: "declined", decisionNote: note });
      }),
    withdraw: (_key, id) =>
      attempt(() => {
        if (find(id).proposedBy !== me) throw new ReissuesError("forbidden");
        return replace(id, { status: "withdrawn" });
      }),
  };
}
