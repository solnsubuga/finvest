/**
 * Requests from clients to close their account, as staff check them.
 *
 * Seeing the queue needs `clients.close`. One person prepares a request with a note, after
 * checking nothing is left; a different person approves it, and only then is the account
 * closed and every session ended. Either may decline it with a note the client reads. The
 * API checks all of this again, and refuses while any money or movement remains.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Currency } from "./api.ts";

export type ClosureStatus = "requested" | "prepared" | "closed" | "declined" | "cancelled";
export type Blocker =
  | "investments_running"
  | "money_owed"
  | "deposits_in_progress"
  | "payouts_in_progress"
  | "goals_open"
  | "plans_live";

export interface Standing {
  runningInvestments: number;
  /** Whole units of the smallest denomination, per currency. */
  owed: { minor: number; currency: Currency }[];
  openDeposits: number;
  openPayouts: number;
  openGoals: number;
  livePlans: number;
  blockers: Blocker[];
}

export interface Closure {
  id: string;
  clientId: string;
  status: ClosureStatus;
  /** The client's own words, if they gave any. */
  reason: string | null;
  requestedAt: string;
  verifiedName: string | null;
  accountNumber: string | null;
  preparedByName: string | null;
  preparedAt: string | null;
  preparedNote: string | null;
  decidedByName: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
  standing: Standing;
  canPrepare: boolean;
  canApprove: boolean;
  canDecline: boolean;
}

export interface ClosuresApi {
  /** Every request still open (requested or prepared), oldest first. */
  open(): Promise<Closure[]>;
  prepare(id: string, note: string): Promise<Closure>;
  approve(id: string, note: string): Promise<Closure>;
  decline(id: string, note: string): Promise<Closure>;
}

export type ClosuresErrorCode =
  | "invalid"
  | "blocked"
  | "moved"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  /** Finvest answered, but refused for another reason, such as too many tries. */
  | "refused"
  /** The request got no answer. */
  | "unavailable";

export class ClosuresError extends Error {
  constructor(readonly code: ClosuresErrorCode) {
    super(code);
    this.name = "ClosuresError";
  }
}

export function closuresErrorMessage(error: unknown): string {
  if (error instanceof ClosuresError) {
    switch (error.code) {
      case "invalid":
        return "Write a short note for the audit trail.";
      case "blocked":
        return "Not done. Something is still open for this client, such as money owed or an investment running. The list below shows where it stands.";
      case "moved":
        return "Something changed while you were looking. The request may have been decided already, or the account was locked. The page now shows where it stands.";
      case "same_person":
        return "You prepared this, so someone else must approve it.";
      case "forbidden":
        return "Your role cannot close accounts.";
      case "not_found":
        return "That request is no longer open.";
      case "not_connected":
        return "This console is not connected to Finvest yet.";
      case "not_confirmed":
        return "Not done. Confirm it is you with your passkey or authenticator app, then try again.";
      case "refused":
        return "Finvest could not do that just now. Try again in a few minutes.";
      case "unavailable":
        return "Finvest could not be reached. Check your connection and try again.";
    }
  }
  return "Something went wrong. Try again.";
}

function toClosuresError(error: unknown, approving = false): ClosuresError {
  if (error instanceof ClosuresError) return error;
  if (error instanceof ApiError) {
    if (error.code === "validation_failed") return new ClosuresError("invalid");
    if (error.code === "conflict") return new ClosuresError(error.fields.some((f) => f.field_path[0] === "blockers") ? "blocked" : "moved");
    if (error.code === "confirm_it_is_you") return new ClosuresError("not_confirmed");
    if (error.code === "forbidden") return new ClosuresError(approving ? "same_person" : "forbidden");
    if (error.code === "not_found") return new ClosuresError("not_found");
    // Status 0 means no answer came back; anything else was a refusal.
    return new ClosuresError(error.status === 0 ? "unavailable" : "refused");
  }
  return new ClosuresError("unavailable");
}

interface StandingJson {
  running_investments: number;
  owed: { amount_minor: number; currency: string }[];
  open_deposits: number;
  open_payouts: number;
  open_goals: number;
  live_plans: number;
  blockers: Blocker[];
}

interface ClosureJson {
  id: string;
  client_id: string;
  status: ClosureStatus;
  reason: string | null;
  requested_at: string;
  decision_note: string | null;
  decided_at: string | null;
  verified_name: string | null;
  account_number: string | null;
  prepared_by_name: string | null;
  prepared_at: string | null;
  prepared_note: string | null;
  decided_by_name: string | null;
  standing: StandingJson;
  can_prepare: boolean;
  can_approve: boolean;
  can_decline: boolean;
}

const CURRENCIES = new Set<string>(["UGX", "USD"]);

export function toClosure(json: ClosureJson): Closure {
  const s = json.standing;
  return {
    id: json.id,
    clientId: json.client_id,
    status: json.status,
    reason: json.reason,
    requestedAt: json.requested_at,
    verifiedName: json.verified_name,
    accountNumber: json.account_number,
    preparedByName: json.prepared_by_name,
    preparedAt: json.prepared_at,
    preparedNote: json.prepared_note,
    decidedByName: json.decided_by_name,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
    standing: {
      runningInvestments: s.running_investments,
      // A currency this console does not know is left out rather than shown wrongly.
      owed: s.owed.filter((m) => CURRENCIES.has(m.currency)).map((m) => ({ minor: m.amount_minor, currency: m.currency as Currency })),
      openDeposits: s.open_deposits,
      openPayouts: s.open_payouts,
      openGoals: s.open_goals,
      livePlans: s.live_plans,
      blockers: s.blockers,
    },
    canPrepare: json.can_prepare,
    canApprove: json.can_approve,
    canDecline: json.can_decline,
  };
}

export function createHttpClosuresApi(http: Http): ClosuresApi {
  const step = (path: string, approving = false) => async (id: string, note: string) => {
    try {
      return toClosure(await http.request<ClosureJson>("POST", `/staff/closures/${encodeURIComponent(id)}/${path}`, { note }));
    } catch (error) {
      throw toClosuresError(error, approving);
    }
  };
  return {
    async open() {
      try {
        return (await http.request<{ items: ClosureJson[] }>("GET", "/staff/closures")).items.map(toClosure);
      } catch (error) {
        throw toClosuresError(error);
      }
    },
    prepare: step("preparation"),
    approve: step("approval", true),
    decline: step("decline"),
  };
}

const refuse = () => Promise.reject(new ClosuresError("not_connected"));

export const notConnectedClosuresApi: ClosuresApi = { open: refuse, prepare: refuse, approve: refuse, decline: refuse };

const CLEAR: Standing = { runningInvestments: 0, owed: [], openDeposits: 0, openPayouts: 0, openGoals: 0, livePlans: 0, blockers: [] };

/** Sample requests for `pnpm dev`. Nothing here is real. The rules follow the API. */
export function createStubClosuresApi(me: { name: string }, now: () => number = Date.now, onClosed: (clientId: string) => void = () => {}, canClose = true): ClosuresApi {
  const ago = (hours: number) => new Date(now() - hours * 60 * 60 * 1000).toISOString();
  const base = { decidedByName: null, decidedAt: null, decisionNote: null, standing: CLEAR, canDecline: canClose };
  const items: (Closure & { preparedByMe: boolean })[] = [
    {
      ...base,
      id: "-NstubClosure0000001",
      clientId: "-NstubClient00000077",
      status: "prepared",
      reason: "Moving abroad for work.",
      requestedAt: ago(26),
      verifiedName: "Peter Mukasa",
      accountNumber: "1004771205",
      preparedByName: "Grace Auma",
      preparedAt: ago(4),
      preparedNote: "Checked: nothing running, last payout arrived on Tuesday.",
      canPrepare: false,
      canApprove: canClose,
      preparedByMe: false,
    },
    {
      ...base,
      id: "-NstubClosure0000002",
      clientId: "-NstubClient00000063",
      status: "requested",
      reason: null,
      requestedAt: ago(2),
      verifiedName: "Ruth Akello",
      accountNumber: "1004630638",
      preparedByName: null,
      preparedAt: null,
      preparedNote: null,
      canPrepare: canClose,
      canApprove: false,
      preparedByMe: false,
    },
  ];
  const wait = () => new Promise((resolve) => setTimeout(resolve, 150));
  const view = (item: Closure & { preparedByMe: boolean }): Closure => {
    const copy: Partial<typeof item> = structuredClone(item);
    delete copy.preparedByMe;
    return copy as Closure;
  };
  const find = (id: string) => {
    const item = items.find((i) => i.id === id && (i.status === "requested" || i.status === "prepared"));
    if (!item) throw new ClosuresError("not_found");
    if (!canClose) throw new ClosuresError("forbidden");
    return item;
  };
  const checkNote = (note: string) => {
    if (!note.trim()) throw new ClosuresError("invalid");
  };
  return {
    async open() {
      await wait();
      if (!canClose) throw new ClosuresError("forbidden");
      return items.filter((i) => i.status === "requested" || i.status === "prepared").map(view);
    },
    async prepare(id, note) {
      await wait();
      const item = find(id);
      checkNote(note);
      if (item.status !== "requested") throw new ClosuresError("moved");
      Object.assign(item, { status: "prepared", preparedByName: me.name, preparedAt: new Date(now()).toISOString(), preparedNote: note.trim(), canPrepare: false, canApprove: false, preparedByMe: true });
      return view(item);
    },
    async approve(id, note) {
      await wait();
      const item = find(id);
      checkNote(note);
      if (item.status !== "prepared") throw new ClosuresError("moved");
      if (item.preparedByMe) throw new ClosuresError("same_person");
      Object.assign(item, { status: "closed", decidedByName: me.name, decidedAt: new Date(now()).toISOString(), decisionNote: note.trim(), canApprove: false, canDecline: false });
      onClosed(item.clientId);
      return view(item);
    },
    async decline(id, note) {
      await wait();
      const item = find(id);
      checkNote(note);
      Object.assign(item, { status: "declined", decidedByName: me.name, decidedAt: new Date(now()).toISOString(), decisionNote: note.trim(), canPrepare: false, canApprove: false, canDecline: false });
      return view(item);
    },
  };
}
