/**
 * What staff do about a term whose money waits for the client: send it to another of the
 * client's approved payout accounts, as a `pay_out` correction that a second person approves
 * (`/staff/investment-corrections`), or write to the client in their help chat
 * (`/staff/support/clients/{id}/messages`). The client's payout accounts come from their client
 * page (`/staff/clients/{id}`). See docs/investing.md, "Undoing a mistake", and docs/support.md.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Correction, CorrectionsApi } from "../investments/corrections/api.ts";
import type { ClientsApi } from "../clients/api.ts";
import { destinationLabel } from "../clients/labels.ts";

export interface PayoutAccount {
  id: string;
  /** "MTN Mobile Money", "Bank (STANBIC)". */
  label: string;
  maskedAccount: string;
  holderName: string;
}

export interface HeldActionsApi {
  /** The client's approved payout accounts. */
  payoutAccounts(clientId: string): Promise<readonly PayoutAccount[]>;
  /** Pay outs of held money, newest first, to show which are waiting for a second person. */
  payOuts(): Promise<readonly Correction[]>;
  proposePayOut(key: string, investmentId: string, destinationId: string, reason: string): Promise<Correction>;
  /** Write to the client in their help chat. They are told there is a message. */
  message(key: string, clientId: string, body: string): Promise<void>;
}

export type MessageErrorCode = "invalid" | "not_found" | "forbidden" | "unavailable";

export class MessageError extends Error {
  constructor(readonly code: MessageErrorCode) {
    super(code);
    this.name = "MessageError";
  }
}

const MESSAGE_ERRORS: Record<MessageErrorCode, string> = {
  invalid: "Finvest did not accept this message. Keep it to plain text of up to 2,000 characters.",
  not_found: "This client can no longer be written to: their account is locked or closed.",
  forbidden: "Your role does not include writing to clients. Ask someone in support.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function messageErrorText(error: unknown): string {
  return MESSAGE_ERRORS[error instanceof MessageError ? error.code : "unavailable"];
}

export function createHttpHeldActionsApi(http: Http, clients: ClientsApi, corrections: CorrectionsApi): HeldActionsApi {
  return {
    async payoutAccounts(clientId) {
      const page = await clients.client(clientId);
      return page.destinations
        .filter((destination) => destination.status === "approved")
        .map((destination) => ({
          id: destination.id,
          label: destinationLabel(destination),
          maskedAccount: destination.maskedAccount,
          holderName: destination.holderName,
        }));
    },
    async payOuts() {
      return (await corrections.list()).filter((correction) => correction.kind === "pay_out");
    },
    proposePayOut: (key, investmentId, destinationId, reason) =>
      corrections.propose(key, { kind: "pay_out", investmentId, destinationId, reason }),
    async message(key, clientId, body) {
      try {
        await http.request("POST", `/staff/support/clients/${encodeURIComponent(clientId)}/messages`, { body, key, topic: "withdrawal" });
      } catch (error) {
        if (error instanceof ApiError) {
          if (error.code === "validation_failed") throw new MessageError("invalid");
          if (error.code === "not_found") throw new MessageError("not_found");
          if (error.code === "forbidden") throw new MessageError("forbidden");
        }
        throw new MessageError("unavailable");
      }
    },
  };
}

/** Sample accounts and messages for `pnpm dev` and previews. Not real clients or money. */
export function createStubHeldActionsApi(corrections: CorrectionsApi): HeldActionsApi {
  const sent: { clientId: string; body: string }[] = [];
  return {
    payoutAccounts: () =>
      Promise.resolve([
        { id: "-DestSample000000011", label: "Airtel Money", maskedAccount: "•••• 4471", holderName: "NAMUTEBI Joan" },
        { id: "-DestSample000000012", label: "Bank (STANBIC)", maskedAccount: "•••• 0915", holderName: "NAMUTEBI Joan" },
      ]),
    async payOuts() {
      return (await corrections.list()).filter((correction) => correction.kind === "pay_out");
    },
    proposePayOut: (key, investmentId, destinationId, reason) =>
      corrections.propose(key, { kind: "pay_out", investmentId, destinationId, reason }),
    message(_key, clientId, body) {
      sent.push({ clientId, body });
      return Promise.resolve();
    },
  };
}
