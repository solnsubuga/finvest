/**
 * The complaints register over the real API (`/staff/complaints…`, docs/support.md).
 *
 * The commands answer with the complaint alone, or the proposed outcome. The screen wants the
 * complaint with its history and any waiting outcome, so each command is followed by a read.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "@/screens/dealing-desk/api.ts";
import {
  ComplaintsError,
  type Category,
  type Complaint,
  type ComplaintDetail,
  type ComplaintsApi,
  type EventAction,
  type Outcome,
  type ReceivedVia,
  type Register,
  type Status,
} from "./api.ts";

interface ComplaintJson {
  id: string;
  reference: string;
  client_id: string;
  conversation_id: string | null;
  category: Category;
  received_via: ReceivedVia;
  summary: string;
  details: string;
  amount: Amount | null;
  status: Status;
  owner_id: string | null;
  received_at: string;
  due_on: string;
  overdue: boolean;
  closed_at: string | null;
  outcome: Outcome | null;
  resolution: string | null;
  client_accepted: boolean | null;
}

interface DetailJson {
  complaint: ComplaintJson;
  history: {
    id: string;
    action: EventAction;
    actor: "client" | "staff";
    actor_id: string;
    from_status: Status | null;
    to_status: Status | null;
    note: string | null;
    occurred_at: string;
  }[];
  pending_outcome: {
    id: string;
    outcome: Outcome;
    resolution: string;
    client_accepted: boolean;
    proposed_by: string;
    proposed_at: string;
  } | null;
}

interface RegisterJson {
  summary: {
    open: number;
    overdue: number;
    received_this_quarter: number;
    closed_this_quarter: number;
    upheld_this_quarter: number;
    average_days_to_close_tenths: number | null;
    target_working_days: number;
  };
  complaints: ComplaintJson[];
}

export function toComplaint(json: ComplaintJson): Complaint {
  return {
    id: json.id,
    reference: json.reference,
    clientId: json.client_id,
    conversationId: json.conversation_id,
    category: json.category,
    receivedVia: json.received_via,
    summary: json.summary,
    details: json.details,
    amount: json.amount ? { minor: json.amount.minor, currency: json.amount.currency } : null,
    status: json.status,
    ownerId: json.owner_id,
    receivedAt: json.received_at,
    dueOn: json.due_on,
    overdue: json.overdue,
    closedAt: json.closed_at,
    outcome: json.outcome,
    resolution: json.resolution,
    clientAccepted: json.client_accepted,
  };
}

export function toDetail(json: DetailJson): ComplaintDetail {
  const pending = json.pending_outcome;
  return {
    complaint: toComplaint(json.complaint),
    history: json.history.map((item) => ({
      id: item.id,
      action: item.action,
      actor: item.actor,
      actorId: item.actor_id,
      fromStatus: item.from_status,
      toStatus: item.to_status,
      note: item.note,
      occurredAt: item.occurred_at,
    })),
    pendingOutcome: pending
      ? {
          id: pending.id,
          outcome: pending.outcome,
          resolution: pending.resolution,
          clientAccepted: pending.client_accepted,
          proposedBy: pending.proposed_by,
          proposedAt: pending.proposed_at,
        }
      : null,
  };
}

export function toRegister(json: RegisterJson): Register {
  const summary = json.summary;
  return {
    summary: {
      open: summary.open,
      overdue: summary.overdue,
      receivedThisQuarter: summary.received_this_quarter,
      closedThisQuarter: summary.closed_this_quarter,
      upheldThisQuarter: summary.upheld_this_quarter,
      averageDaysToCloseTenths: summary.average_days_to_close_tenths,
      targetWorkingDays: summary.target_working_days,
    },
    complaints: json.complaints.map(toComplaint),
  };
}

export function toComplaintsError(error: unknown): ComplaintsError {
  if (error instanceof ComplaintsError) return error;
  if (!(error instanceof ApiError)) return new ComplaintsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new ComplaintsError("invalid");
    case "not_found":
      return new ComplaintsError("not_found");
    // Closed, an outcome already waiting, or a key sent again with other details.
    case "conflict":
      return new ComplaintsError("conflict");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new ComplaintsError("not_confirmed");
    case "forbidden":
      return new ComplaintsError("forbidden");
    default:
      return new ComplaintsError("unavailable");
  }
}

export function createHttpComplaintsApi(http: Http): ComplaintsApi {
  const path = (id: string, action = "") => `/staff/complaints/${encodeURIComponent(id)}${action}`;
  const detail = async (id: string) => toDetail(await http.request<DetailJson>("GET", path(id)));

  /**
   * Runs the command, then reads the complaint as it now stands. Once the command has gone
   * through, a failed read is `stale`, never an error that would have the person send it again.
   */
  async function command(run: () => Promise<{ id: string } | string>): Promise<ComplaintDetail> {
    let done: { id: string } | string;
    try {
      done = await run();
    } catch (error) {
      throw toComplaintsError(error);
    }
    try {
      return await detail(typeof done === "string" ? done : done.id);
    } catch {
      throw new ComplaintsError("stale");
    }
  }

  return {
    async register(show) {
      try {
        return toRegister(await http.request<RegisterJson>("GET", `/staff/complaints?${new URLSearchParams({ show }).toString()}`));
      } catch (error) {
        throw toComplaintsError(error);
      }
    },
    async complaint(complaintId) {
      try {
        return await detail(complaintId);
      } catch (error) {
        throw toComplaintsError(error);
      }
    },
    log: (complaint) =>
      command(() =>
        http.request<ComplaintJson>("POST", "/staff/complaints", {
          client_key: complaint.clientKey,
          client_id: complaint.clientId,
          category: complaint.category,
          received_via: complaint.receivedVia,
          summary: complaint.summary,
          details: complaint.details,
          amount: complaint.amount,
        }),
      ),
    assign: (complaintId, ownerId) => command(() => http.request<ComplaintJson>("PUT", path(complaintId, "/owner"), { owner_id: ownerId })),
    move: (complaintId, status, note) => command(() => http.request<ComplaintJson>("POST", path(complaintId, "/status"), { status, note })),
    addNote: (complaintId, note) => command(() => http.request<ComplaintJson>("POST", path(complaintId, "/notes"), { note })),
    proposeOutcome: (complaintId, closing) =>
      command(async () => {
        await http.request("POST", path(complaintId, "/outcome"), {
          outcome: closing.outcome,
          resolution: closing.resolution,
          client_accepted: closing.clientAccepted,
        });
        return complaintId;
      }),
    decideOutcome: (complaintId, proposalId, decision, reason) =>
      command(() => http.request<ComplaintJson>("POST", path(complaintId, "/outcome/decision"), { proposal_id: proposalId, decision, reason })),
  };
}
