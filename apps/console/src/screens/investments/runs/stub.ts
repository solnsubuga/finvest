import { RunRequestsError, type NewRunRequest, type RunRequest, type RunRequestsApi } from "./api.ts";

/**
 * Sample run requests for `pnpm dev` and previews. It keeps the backend's rules that a screen
 * can show: the proposer never decides, one open request per run and day, interest for today
 * back 31 days and maturity for today only.
 */

const OTHER = "sample-staff-grace";

function daysBefore(today: string, days: number): string {
  const date = new Date(`${today}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function samples(me: string, today: string): RunRequest[] {
  const base = { decidedBy: null, decidedAt: null, decisionNote: null };
  return [
    {
      ...base,
      id: "-RunSample0000000001",
      kind: "interest",
      runDate: daysBefore(today, 1),
      reason: "Yesterday's run stopped after 25 investments failed in a row while the ledger day was closed. It is open again.",
      status: "proposed",
      proposedBy: OTHER,
      proposedAt: `${today}T05:12:00Z`,
    },
    {
      ...base,
      id: "-RunSample0000000002",
      kind: "maturity",
      runDate: today,
      reason: "Three terms were left open after this morning's run. Engineering fixed the payout account check.",
      status: "proposed",
      proposedBy: me,
      proposedAt: `${today}T06:40:00Z`,
    },
    {
      ...base,
      id: "-RunSample0000000003",
      kind: "interest",
      runDate: daysBefore(today, 4),
      reason: "The run was cut short by the database restart.",
      status: "approved",
      proposedBy: me,
      proposedAt: `${daysBefore(today, 3)}T07:05:00Z`,
      decidedBy: OTHER,
      decidedAt: `${daysBefore(today, 3)}T07:31:00Z`,
    },
  ];
}

export function createStubRunRequestsApi(
  me: string,
  sample: "default" | "empty" = "default",
  today: () => string = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date()),
  now: () => string = () => new Date().toISOString(),
): RunRequestsApi {
  let items = sample === "empty" ? [] : samples(me, today());
  let next = 1;

  function find(id: string): RunRequest {
    const found = items.find((item) => item.id === id);
    if (!found) throw new RunRequestsError("not_found");
    if (found.status !== "proposed") throw new RunRequestsError("conflict");
    return found;
  }

  function replace(id: string, change: Partial<RunRequest>): RunRequest {
    items = items.map((item) => (item.id === id ? { ...item, ...change, decidedBy: me, decidedAt: now() } : item));
    return items.find((item) => item.id === id) as RunRequest;
  }

  return {
    list: () => Promise.resolve([...items].sort((a, b) => b.proposedAt.localeCompare(a.proposedAt))),
    propose(_key: string, request: NewRunRequest) {
      const day = today();
      if (request.runDate > day) return Promise.reject(new RunRequestsError("invalid", [{ field: "run_date", code: "not_yet" }]));
      if (request.kind === "maturity" && request.runDate !== day) {
        return Promise.reject(new RunRequestsError("invalid", [{ field: "run_date", code: "maturity_today_only" }]));
      }
      if (request.kind === "interest" && request.runDate < daysBefore(day, 31)) {
        return Promise.reject(new RunRequestsError("invalid", [{ field: "run_date", code: "too_old" }]));
      }
      if (items.some((item) => item.status === "proposed" && item.kind === request.kind && item.runDate === request.runDate)) {
        return Promise.reject(new RunRequestsError("conflict"));
      }
      const created: RunRequest = {
        id: `-RunNew${String(next++).padStart(13, "0")}`,
        ...request,
        status: "proposed",
        proposedBy: me,
        proposedAt: now(),
        decidedBy: null,
        decidedAt: null,
        decisionNote: null,
      };
      items = [created, ...items];
      return Promise.resolve(created);
    },
    approve(_key, id) {
      try {
        if (find(id).proposedBy === me) return Promise.reject(new RunRequestsError("same_person"));
        return Promise.resolve(replace(id, { status: "approved" }));
      } catch (error) {
        return Promise.reject(error);
      }
    },
    decline(_key, id, note) {
      try {
        if (find(id).proposedBy === me) return Promise.reject(new RunRequestsError("same_person"));
        return Promise.resolve(replace(id, { status: "declined", decisionNote: note }));
      } catch (error) {
        return Promise.reject(error);
      }
    },
    withdraw(_key, id) {
      try {
        if (find(id).proposedBy !== me) return Promise.reject(new RunRequestsError("forbidden"));
        return Promise.resolve(replace(id, { status: "withdrawn" }));
      } catch (error) {
        return Promise.reject(error);
      }
    },
  };
}
