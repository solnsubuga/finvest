/**
 * Investments sent from one client to another and held for two members of staff because they
 * are large (docs/investing.md, "Sending an investment to another client"). One person
 * proposes letting it through, a different person approves, and one person may decline.
 *
 * The routes are `/staff/investment-transfers/held` and `/staff/investment-transfers/{id}/release`,
 * `/approve`, `/withdraw` and `/decline`. `createHttpTransfersApi` calls them and
 * `createStubTransfersApi` is the sample for `pnpm dev` and previews. Not real people.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "./api.ts";

export type TransferAction = "propose" | "approve" | "withdraw" | "decline";

export interface HeldTransfer {
  id: string;
  investmentId: string;
  senderClientId: string;
  recipientClientId: string;
  /** As the clients see each other: "Brian K.". The directory gives staff the full name. */
  senderName: string;
  recipientName: string;
  amount: Amount;
  /** Everything was asked for, including interest added before it moves. */
  whole: boolean;
  message: string | null;
  status: "held" | "release_proposed" | "waiting" | "declined" | "done" | "failed" | "cancelled";
  requestedAt: string;
  release: { proposedBy: string; proposedAt: string; reason: string } | null;
}

export type TransfersErrorCode = "conflict" | "forbidden" | "not_found" | "key_reused" | "not_confirmed" | "invalid" | "not_connected" | "unavailable";

export class TransfersError extends Error {
  readonly code: TransfersErrorCode;

  constructor(code: TransfersErrorCode) {
    super(code);
    this.name = "TransfersError";
    this.code = code;
  }
}

const MESSAGES: Record<TransfersErrorCode, string> = {
  conflict: "Someone has already acted on this transfer, or the client cancelled it. The list has been reloaded.",
  forbidden:
    "A different person must do this: the one who proposed a release cannot approve it, and only they can withdraw it.",
  not_found: "This transfer no longer exists.",
  key_reused: "This decision was already sent with other details. Reload the page and decide again.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  invalid: "Finvest refused what you typed. Keep the reason under 500 characters and send again.",
  not_connected: "Held transfers are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function transfersErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof TransfersError ? error.code : "unavailable"];
}

export interface TransfersApi {
  /** Held transfers, oldest first. */
  listHeld(): Promise<HeldTransfer[]>;
  proposeRelease(id: string, reason: string, idempotencyKey: string): Promise<HeldTransfer>;
  approveRelease(id: string, reason: string, idempotencyKey: string): Promise<HeldTransfer>;
  withdrawRelease(id: string): Promise<HeldTransfer>;
  decline(id: string, reason: string, idempotencyKey: string): Promise<HeldTransfer>;
}

const notConnected = () => Promise.reject(new TransfersError("not_connected"));

export const notConnectedTransfersApi: TransfersApi = {
  listHeld: notConnected,
  proposeRelease: notConnected,
  approveRelease: notConnected,
  withdrawRelease: notConnected,
  decline: notConnected,
};

export interface TransferJson {
  id: string;
  investment_id: string;
  sender_client_id: string;
  recipient_client_id: string;
  sender_name: string;
  recipient_name: string;
  amount_minor: number;
  currency: "UGX" | "USD";
  whole: boolean;
  message: string | null;
  status: HeldTransfer["status"];
  requested_at: string;
  release_proposed_by: string | null;
  release_proposed_at: string | null;
  release_reason: string | null;
}

export function toHeldTransfer(json: TransferJson): HeldTransfer {
  return {
    id: json.id,
    investmentId: json.investment_id,
    senderClientId: json.sender_client_id,
    recipientClientId: json.recipient_client_id,
    senderName: json.sender_name,
    recipientName: json.recipient_name,
    amount: { minor: json.amount_minor, currency: json.currency },
    whole: json.whole,
    message: json.message,
    status: json.status,
    requestedAt: json.requested_at,
    release:
      json.release_proposed_by && json.release_proposed_at && json.release_reason
        ? { proposedBy: json.release_proposed_by, proposedAt: json.release_proposed_at, reason: json.release_reason }
        : null,
  };
}

export function toTransfersError(error: unknown): TransfersError {
  if (error instanceof TransfersError) return error;
  if (!(error instanceof ApiError)) return new TransfersError("unavailable");
  switch (error.code) {
    case "conflict":
      return new TransfersError("conflict");
    case "forbidden":
      return new TransfersError("forbidden");
    case "not_found":
      return new TransfersError("not_found");
    case "idempotency_key_reused":
      return new TransfersError("key_reused");
    case "confirm_it_is_you":
      return new TransfersError("not_confirmed");
    case "validation_failed":
      return new TransfersError("invalid");
    default:
      return new TransfersError("unavailable");
  }
}

export function createHttpTransfersApi(http: Http): TransfersApi {
  const path = (id: string, action: string) => `/staff/investment-transfers/${encodeURIComponent(id)}/${action}`;
  async function call(run: () => Promise<TransferJson>): Promise<HeldTransfer> {
    try {
      return toHeldTransfer(await run());
    } catch (error) {
      throw toTransfersError(error);
    }
  }
  return {
    listHeld: async () => {
      try {
        return (await http.request<TransferJson[]>("GET", "/staff/investment-transfers/held")).map(toHeldTransfer);
      } catch (error) {
        throw toTransfersError(error);
      }
    },
    proposeRelease: (id, reason, idempotencyKey) =>
      call(() => http.request<TransferJson>("POST", path(id, "release"), { reason }, { idempotencyKey })),
    approveRelease: (id, reason, idempotencyKey) =>
      call(() => http.request<TransferJson>("POST", path(id, "approve"), { reason }, { idempotencyKey })),
    withdrawRelease: (id) => call(() => http.request<TransferJson>("POST", path(id, "withdraw"))),
    decline: (id, reason, idempotencyKey) =>
      call(() => http.request<TransferJson>("POST", path(id, "decline"), { reason }, { idempotencyKey })),
  };
}

const HOUR = 3_600_000;

/**
 * Sample held transfers: one waiting for a first person, and one a colleague proposed. It
 * refuses what the backend refuses: approving your own proposal, withdrawing someone else's,
 * and acting on a transfer that is no longer held.
 */
export function createStubTransfersApi(me: string, clock: () => number = Date.now, sample: "sample" | "empty" | "error" = "sample"): TransfersApi {
  if (sample === "error") {
    const failure = () => Promise.reject(new TransfersError("unavailable"));
    return { listHeld: failure, proposeRelease: failure, approveRelease: failure, withdrawRelease: failure, decline: failure };
  }
  const now = clock();
  const held: HeldTransfer[] =
    sample === "empty"
      ? []
      : [
          {
            id: "sample-transfer-1",
            investmentId: "sample-investment-1",
            senderClientId: "sample-client-joan",
            recipientClientId: "sample-client-sarah",
            senderName: "Joan N.",
            recipientName: "Sarah A.",
            amount: { minor: 15_000_000, currency: "UGX" },
            whole: false,
            message: "For the land in Mukono",
            status: "held",
            requestedAt: new Date(now - 3 * HOUR).toISOString(),
            release: null,
          },
          {
            id: "sample-transfer-2",
            investmentId: "sample-investment-2",
            senderClientId: "sample-client-peter",
            recipientClientId: "sample-client-amara",
            senderName: "Peter O.",
            recipientName: "Amara N.",
            amount: { minor: 24_310_455, currency: "UGX" },
            whole: true,
            message: null,
            status: "release_proposed",
            requestedAt: new Date(now - 26 * HOUR).toISOString(),
            release: {
              proposedBy: "sample-staff-daniel",
              proposedAt: new Date(now - 20 * HOUR).toISOString(),
              reason: "Called the sender on their number on file. Paying his sister for her school fees.",
            },
          },
        ];
  const answered = new Map<string, { request: string; answer: HeldTransfer }>();
  const pause = () => new Promise<void>((resolve) => setTimeout(resolve, 250));
  const find = (id: string) => {
    const transfer = held.find((candidate) => candidate.id === id);
    if (!transfer) throw new TransfersError("not_found");
    if (transfer.status !== "held" && transfer.status !== "release_proposed") throw new TransfersError("conflict");
    return transfer;
  };
  const once = async (key: string, request: object, run: () => HeldTransfer): Promise<HeldTransfer> => {
    await pause();
    const details = JSON.stringify(request);
    const known = answered.get(key);
    if (known) {
      if (known.request !== details) throw new TransfersError("key_reused");
      return { ...known.answer };
    }
    const answer = run();
    answered.set(key, { request: details, answer: { ...answer } });
    return { ...answer };
  };
  const remove = (transfer: HeldTransfer) => held.splice(held.indexOf(transfer), 1);
  return {
    listHeld: async () => {
      await pause();
      return held.filter((one) => one.status === "held" || one.status === "release_proposed").map((one) => ({ ...one }));
    },
    proposeRelease: (id, reason, key) =>
      once(key, { step: "propose", id, reason }, () => {
        const transfer = find(id);
        if (transfer.release) throw new TransfersError("conflict");
        transfer.status = "release_proposed";
        transfer.release = { proposedBy: me, proposedAt: new Date(clock()).toISOString(), reason };
        return transfer;
      }),
    approveRelease: (id, reason, key) =>
      once(key, { step: "approve", id, reason }, () => {
        const transfer = find(id);
        if (!transfer.release) throw new TransfersError("conflict");
        if (transfer.release.proposedBy === me) throw new TransfersError("forbidden");
        transfer.status = "waiting";
        remove(transfer);
        return transfer;
      }),
    withdrawRelease: async (id) => {
      await pause();
      const transfer = find(id);
      if (!transfer.release) throw new TransfersError("conflict");
      if (transfer.release.proposedBy !== me) throw new TransfersError("forbidden");
      transfer.status = "held";
      transfer.release = null;
      return { ...transfer };
    },
    decline: (id, reason, key) =>
      once(key, { step: "decline", id, reason }, () => {
        const transfer = find(id);
        transfer.status = "declined";
        remove(transfer);
        return transfer;
      }),
  };
}
