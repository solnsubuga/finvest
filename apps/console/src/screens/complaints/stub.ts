import {
  ComplaintsError,
  MAX_DETAILS,
  MAX_NOTE,
  MAX_RESOLUTION,
  MAX_SUMMARY,
  type Complaint,
  type ComplaintDetail,
  type ComplaintsApi,
  type HistoryItem,
  type PendingOutcome,
  type RejectedOutcome,
  type Register,
  type Status,
} from "./api.ts";
import { kampalaDate } from "./labels.ts";

/**
 * A sample register for `pnpm dev` and previews, with the complaints drawn on
 * the canvas. Not real clients and not connected to Finvest. Changes stay in
 * this browser tab.
 */

const TARGET_WORKING_DAYS = 5;

/** The calendar date `days` working days after `date`, Monday to Friday, as the backend counts it. */
export function addWorkingDays(date: string, days: number): string {
  const day = new Date(`${date}T00:00:00Z`);
  let left = days;
  while (left > 0) {
    day.setUTCDate(day.getUTCDate() + 1);
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) left -= 1;
  }
  return day.toISOString().slice(0, 10);
}

/** Working days from one calendar date to a later one. */
export function workingDaysBetween(start: string, end: string): number {
  const day = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  let days = 0;
  while (day < last) {
    day.setUTCDate(day.getUTCDate() + 1);
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) days += 1;
  }
  return days;
}

function quarterStart(today: string): string {
  const [year, month] = today.split("-").map(Number) as [number, number];
  const first = Math.floor((month - 1) / 3) * 3 + 1;
  return `${year}-${String(first).padStart(2, "0")}-01`;
}

interface Stored {
  complaint: Complaint;
  history: HistoryItem[];
  pending: PendingOutcome | null;
  rejected?: RejectedOutcome | null;
  clientKey: string | null;
}

export function createStubComplaintsApi(
  me: { id: string; name: string },
  today: () => string,
  now: () => number = Date.now,
  seed: "sample" | "empty" = "sample",
): ComplaintsApi {
  let counter = 0;
  const id = (prefix: string) => `${prefix}-${++counter}`;
  let nextReference = 2618;

  function sample(
    reference: string,
    clientId: string,
    fields: Pick<Complaint, "category" | "receivedVia" | "summary" | "details" | "status" | "ownerId"> &
      Partial<Pick<Complaint, "amount" | "conversationId" | "closedAt" | "outcome" | "resolution" | "clientAccepted">>,
    receivedOn: string,
    history: [Exclude<HistoryItem["action"], "logged">, string, Status | null, string | null, string][] = [],
  ): Stored {
    const receivedAt = `${receivedOn}T07:20:00Z`;
    const complaint: Complaint = {
      id: id("sample-complaint"),
      reference,
      clientId,
      conversationId: null,
      amount: null,
      closedAt: null,
      outcome: null,
      resolution: null,
      clientAccepted: null,
      ...fields,
      receivedAt,
      dueOn: addWorkingDays(receivedOn, TARGET_WORKING_DAYS),
      overdue: false,
    };
    let status: Status = "new";
    const events: HistoryItem[] = [
      {
        id: id("sample-event"),
        action: "logged",
        // A client logs their own in the app. Staff log the rest.
        actor: fields.receivedVia === "app" ? "client" : "staff",
        actorId: fields.receivedVia === "app" ? clientId : "sample-staff-grace",
        fromStatus: null,
        toStatus: "new",
        note: null,
        occurredAt: receivedAt,
      },
    ];
    for (const [action, actorId, toStatus, note, occurredAt] of history) {
      events.push({
        id: id("sample-event"),
        action,
        actor: "staff",
        actorId,
        fromStatus: toStatus ? status : null,
        toStatus,
        note,
        occurredAt,
      });
      if (toStatus) status = toStatus;
    }
    return { complaint, history: events, pending: null, clientKey: null };
  }

  const samples: Stored[] = [
    sample(
      "C-2611",
      "sample-client-joan",
      {
        category: "payments",
        receivedVia: "phone",
        summary: "Payout did not arrive",
        details: "My maturity payout was due on Thursday and nothing has reached my MTN number.",
        amount: { minor: 1_026_490, currency: "UGX" },
        status: "investigating",
        ownerId: "sample-staff-grace",
      },
      "2026-11-05",
      [
        ["assigned", "sample-staff-grace", null, null, "2026-11-05T08:00:00Z"],
        ["status", "sample-staff-grace", "investigating", "MTN returned an error on the first try. Asked them why.", "2026-11-05T09:10:00Z"],
      ],
    ),
    sample(
      "C-2614",
      "sample-client-peter",
      {
        category: "payments",
        receivedVia: "chat",
        conversationId: "sample-chat-2",
        summary: "Bank deposit not matched",
        details: "I sent 2m by bank on Monday and it is not showing in the app.",
        amount: { minor: 2_000_000, currency: "UGX" },
        status: "investigating",
        ownerId: "sample-staff-grace",
      },
      "2026-11-09",
      [["status", "sample-staff-grace", "investigating", "Looking for the transfer in the bank statement.", "2026-11-09T10:00:00Z"]],
    ),
    sample(
      "C-2615",
      "sample-client-sarah",
      {
        category: "investments",
        receivedVia: "email",
        summary: "Rate was lower than expected",
        details: "The app showed 11.2% when I invested, but my statement shows less interest than that.",
        status: "investigating",
        ownerId: "sample-staff-daniel",
      },
      "2026-11-10",
      [
        ["status", "sample-staff-daniel", "investigating", "Checking the rate on the day she invested.", "2026-11-10T12:30:00Z"],
        ["proposed", "sample-staff-daniel", null, null, "2026-11-12T09:40:00Z"],
      ],
    ),
    sample(
      "C-2616",
      "sample-client-ivan",
      {
        category: "account",
        receivedVia: "phone",
        summary: "Could not sign in on a new phone",
        details: "I changed my phone and the app keeps asking for a code I never receive.",
        status: "waiting_for_client",
        ownerId: null,
      },
      "2026-11-11",
      [["status", "sample-staff-daniel", "waiting_for_client", "Asked him to visit the office with his ID.", "2026-11-11T13:00:00Z"]],
    ),
    sample(
      "C-2617",
      "sample-client-kira",
      {
        category: "account",
        receivedVia: "letter",
        summary: "Signatory change taking too long",
        details: "We wrote two weeks ago to change our group's signatories and it is still not done.",
        status: "new",
        ownerId: "sample-staff-grace",
      },
      "2026-11-11",
    ),
    sample(
      "C-2609",
      "sample-client-amara",
      {
        category: "account",
        receivedVia: "app",
        summary: "Statement missing a transaction",
        details: "My October statement does not show the deposit I made on 28 October.",
        status: "closed",
        ownerId: "sample-staff-daniel",
        closedAt: "2026-11-11T11:00:00Z",
        outcome: "upheld",
        resolution: "We added the missing deposit to your October statement and sent it to you again.",
        clientAccepted: true,
      },
      "2026-11-06",
      [
        ["note", "sample-staff-daniel", null, "The statement job skipped deposits matched after the month closed.", "2026-11-09T09:00:00Z"],
        ["proposed", "sample-staff-daniel", null, null, "2026-11-11T10:30:00Z"],
        ["closed", "sample-staff-grace", "closed", null, "2026-11-11T11:00:00Z"],
      ],
    ),
  ];

  // Daniel proposed an outcome for C-2615, so it waits for someone else to confirm it.
  const waiting = samples[2] as Stored;
  waiting.pending = {
    id: id("sample-proposal"),
    outcome: "not_upheld",
    resolution: "The rate you saw was right. Your statement shows the interest after tax, so the figure is lower. Nothing is missing.",
    clientAccepted: true,
    proposedBy: "sample-staff-daniel",
    proposedAt: "2026-11-12T09:40:00Z",
  };

  const stored = seed === "empty" ? [] : samples;
  const isOverdue = (complaint: Complaint) => complaint.status !== "closed" && today() > complaint.dueOn;
  const copy = ({ complaint, history, pending, rejected }: Stored): ComplaintDetail => ({
    complaint: { ...complaint, overdue: isOverdue(complaint) },
    history: history.map((item) => ({ ...item })),
    pendingOutcome: pending ? { ...pending } : null,
    lastRejected: rejected ? { ...rejected } : null,
  });
  const loggedBy = (item: Stored) => {
    const logged = item.history.find((event) => event.action === "logged");
    return logged?.actor === "staff" ? logged.actorId : null;
  };
  const find = (complaintId: string): Stored => {
    const found = stored.find((item) => item.complaint.id === complaintId);
    if (!found) throw new ComplaintsError("not_found");
    return found;
  };
  const record = (item: Stored, event: Omit<HistoryItem, "id" | "actor" | "actorId" | "occurredAt">) => {
    item.history.push({ ...event, id: id("sample-event"), actor: "staff", actorId: me.id, occurredAt: new Date(now()).toISOString() });
  };
  const clean = (text: string, max: number) => {
    const value = text.trim();
    if (value.length === 0 || value.length > max) throw new ComplaintsError("invalid");
    return value;
  };
  /** Runs a command on an open complaint and answers as the API would. */
  const change = (complaintId: string, apply: (item: Stored) => void): Promise<ComplaintDetail> => {
    try {
      const item = find(complaintId);
      if (item.complaint.status === "closed") throw new ComplaintsError("conflict");
      apply(item);
      return Promise.resolve(copy(item));
    } catch (error) {
      return Promise.reject(error);
    }
  };

  return {
    register(show) {
      const all = stored.map((item) => copy(item).complaint);
      const since = quarterStart(today());
      const closedThisQuarter = all.filter((complaint) => complaint.closedAt && kampalaDate(complaint.closedAt) >= since);
      const days = closedThisQuarter.map((complaint) =>
        workingDaysBetween(kampalaDate(complaint.receivedAt), kampalaDate(complaint.closedAt as string)),
      );
      const register: Register = {
        summary: {
          open: all.filter((complaint) => complaint.status !== "closed").length,
          overdue: all.filter((complaint) => complaint.overdue).length,
          receivedThisQuarter: all.filter((complaint) => kampalaDate(complaint.receivedAt) >= since).length,
          closedThisQuarter: closedThisQuarter.length,
          upheldThisQuarter: closedThisQuarter.filter((complaint) => complaint.outcome === "upheld").length,
          averageDaysToCloseTenths:
            days.length === 0 ? null : Math.round((days.reduce((sum, value) => sum + value, 0) * 10) / days.length),
          targetWorkingDays: TARGET_WORKING_DAYS,
        },
        complaints: all
          .filter((complaint) => show === "all" || (show === "open") === (complaint.status !== "closed"))
          // Open ones by deadline, closed ones most recent first, as the register reads.
          .sort((a, b) => {
            const aClosed = a.status === "closed";
            const bClosed = b.status === "closed";
            if (aClosed !== bClosed) return aClosed ? 1 : -1;
            if (aClosed) return (b.closedAt ?? "").localeCompare(a.closedAt ?? "");
            return a.dueOn.localeCompare(b.dueOn) || a.reference.localeCompare(b.reference);
          }),
      };
      return Promise.resolve(register);
    },
    complaint(complaintId) {
      try {
        return Promise.resolve(copy(find(complaintId)));
      } catch (error) {
        return Promise.reject(error);
      }
    },
    log(input) {
      try {
        if (!/^[A-Za-z0-9._:-]{8,64}$/.test(input.clientKey)) throw new ComplaintsError("invalid");
        // A repeated key answers with the complaint it logged, as the API does.
        const again = stored.find((item) => item.clientKey === input.clientKey && loggedBy(item) === me.id);
        if (again) return Promise.resolve(copy(again));
        const receivedAt = new Date(now()).toISOString();
        const item: Stored = {
          complaint: {
            id: id("sample-complaint"),
            reference: `C-${nextReference++}`,
            clientId: input.clientId,
            conversationId: null,
            category: input.category,
            receivedVia: input.receivedVia,
            summary: clean(input.summary, MAX_SUMMARY),
            details: clean(input.details, MAX_DETAILS),
            amount: input.amount,
            status: "new",
            ownerId: null,
            receivedAt,
            dueOn: addWorkingDays(today(), TARGET_WORKING_DAYS),
            overdue: false,
            closedAt: null,
            outcome: null,
            resolution: null,
            clientAccepted: null,
          },
          history: [],
          pending: null,
          clientKey: input.clientKey,
        };
        record(item, { action: "logged", fromStatus: null, toStatus: "new", note: null });
        stored.push(item);
        return Promise.resolve(copy(item));
      } catch (error) {
        return Promise.reject(error);
      }
    },
    assign: (complaintId, ownerId) =>
      change(complaintId, (item) => {
        item.complaint.ownerId = ownerId;
        record(item, { action: "assigned", fromStatus: null, toStatus: null, note: null });
      }),
    move: (complaintId, status, note) =>
      change(complaintId, (item) => {
        const text = clean(note, MAX_NOTE);
        record(item, {
          action: status === "escalated" ? "escalated" : "status",
          fromStatus: item.complaint.status,
          toStatus: status,
          note: text,
        });
        item.complaint.status = status;
      }),
    addNote: (complaintId, note) =>
      change(complaintId, (item) => {
        record(item, { action: "note", fromStatus: null, toStatus: null, note: clean(note, MAX_NOTE) });
      }),
    proposeOutcome: (complaintId, closing) =>
      change(complaintId, (item) => {
        if (item.pending) throw new ComplaintsError("conflict");
        item.rejected = null;
        item.pending = {
          id: id("sample-proposal"),
          outcome: closing.outcome,
          resolution: clean(closing.resolution, MAX_RESOLUTION),
          clientAccepted: closing.clientAccepted,
          proposedBy: me.id,
          proposedAt: new Date(now()).toISOString(),
        };
        record(item, { action: "proposed", fromStatus: null, toStatus: null, note: null });
      }),
    decideOutcome: (complaintId, proposalId, decision, reason) =>
      change(complaintId, (item) => {
        const pending = item.pending;
        if (!pending || pending.id !== proposalId) throw new ComplaintsError("conflict");
        const mine = pending.proposedBy === me.id;
        // The same rules PostgreSQL holds: the proposer only withdraws, and whoever logged it never confirms.
        if ((decision === "withdrawn") !== mine) throw new ComplaintsError("forbidden");
        if (decision === "confirmed" && loggedBy(item) === me.id) throw new ComplaintsError("forbidden");
        const note = decision === "rejected" || (reason ?? "").trim() ? clean(reason ?? "", MAX_NOTE) : null;
        item.pending = null;
        item.rejected =
          decision === "rejected"
            ? {
                outcome: pending.outcome,
                resolution: pending.resolution,
                clientAccepted: pending.clientAccepted,
                decidedBy: me.id,
                decidedAt: new Date(now()).toISOString(),
                reason: note ?? "",
              }
            : null;
        if (decision !== "confirmed") {
          record(item, { action: decision, fromStatus: null, toStatus: null, note });
          return;
        }
        record(item, { action: "closed", fromStatus: item.complaint.status, toStatus: "closed", note: null });
        Object.assign(item.complaint, {
          status: "closed",
          closedAt: new Date(now()).toISOString(),
          outcome: pending.outcome,
          resolution: pending.resolution,
          clientAccepted: pending.clientAccepted,
        });
      }),
  };
}
