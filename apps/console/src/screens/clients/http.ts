import type { Http } from "@/api/http.ts";
import { toClientsError, type Account, type ClientPage, type ClientsApi, type Currency, type Lock } from "./api.ts";

interface AccountJson {
  id: string;
  phone: string;
  email: string | null;
  status: Account["status"];
  created_at: string;
  restricted_until: string | null;
  has_passkey: boolean;
  has_authenticator: boolean;
  last_signed_in_at: string | null;
}

interface LockJson {
  id: string;
  kind: Lock["kind"];
  client_id: string;
  reason: string;
  proposed_by: string;
  proposed_by_name: string;
  proposed_at: string;
  status: Lock["status"];
  decided_by: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  yours: boolean;
}

interface PageJson {
  account: AccountJson;
  verified_name: string | null;
  identity_check: { id: string; status: string; submitted_at: string | null; decided_at: string | null; account_number: string | null } | null;
  owed: { account_code: string; account_reference: string | null; amount_minor: number; currency: string }[];
  destinations: {
    id: string;
    kind: string;
    network: string | null;
    bank_code: string | null;
    masked_account: string;
    holder_name: string;
    status: string;
    created_at: string;
  }[];
  locks: LockJson[];
  conversation: { id: string; topic: string; status: string; last_message_at: string; waiting_since: string | null } | null;
  complaints:
    | { id: string; reference: string; category: string; summary: string; status: string; received_at: string; due_on: string; overdue: boolean }[]
    | null;
  can_lock: boolean;
}

function toAccount(json: AccountJson): Account {
  return {
    id: json.id,
    phone: json.phone,
    email: json.email,
    status: json.status,
    createdAt: json.created_at,
    restrictedUntil: json.restricted_until,
    hasPasskey: json.has_passkey,
    hasAuthenticator: json.has_authenticator,
    lastSignedInAt: json.last_signed_in_at,
  };
}

export function toLock(json: LockJson): Lock {
  return {
    id: json.id,
    kind: json.kind,
    reason: json.reason,
    proposedByName: json.proposed_by_name,
    proposedAt: json.proposed_at,
    status: json.status,
    decidedByName: json.decided_by_name,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    yours: json.yours,
  };
}

const CURRENCIES = new Set<string>(["UGX", "USD"]);

export function toPage(json: PageJson): ClientPage {
  const check = json.identity_check;
  return {
    account: toAccount(json.account),
    verifiedName: json.verified_name,
    identityCheck: check
      ? { id: check.id, status: check.status, submittedAt: check.submitted_at, decidedAt: check.decided_at, accountNumber: check.account_number }
      : null,
    // A currency this console does not know is left out rather than shown wrongly.
    owed: json.owed
      .filter((item) => CURRENCIES.has(item.currency))
      .map((item) => ({ accountCode: item.account_code, accountReference: item.account_reference, minor: item.amount_minor, currency: item.currency as Currency })),
    destinations: json.destinations.map((item) => ({
      id: item.id,
      kind: item.kind,
      network: item.network,
      bankCode: item.bank_code,
      maskedAccount: item.masked_account,
      holderName: item.holder_name,
      status: item.status,
      createdAt: item.created_at,
    })),
    locks: json.locks.map(toLock),
    conversation: json.conversation
      ? {
          id: json.conversation.id,
          topic: json.conversation.topic,
          status: json.conversation.status,
          lastMessageAt: json.conversation.last_message_at,
          waitingSince: json.conversation.waiting_since,
        }
      : null,
    complaints:
      json.complaints?.map((item) => ({
        id: item.id,
        reference: item.reference,
        category: item.category,
        summary: item.summary,
        status: item.status,
        receivedAt: item.received_at,
        dueOn: item.due_on,
        overdue: item.overdue,
      })) ?? null,
    canLock: json.can_lock,
  };
}

export function createHttpClientsApi(http: Http): ClientsApi {
  return {
    async search(query) {
      try {
        const found = await http.request<{ clients: AccountJson[] }>("GET", `/staff/clients/search?${new URLSearchParams({ q: query.trim() }).toString()}`);
        return found.clients.map(toAccount);
      } catch (error) {
        throw toClientsError(error);
      }
    },
    async client(id) {
      try {
        return toPage(await http.request<PageJson>("GET", `/staff/clients/${encodeURIComponent(id)}`));
      } catch (error) {
        throw toClientsError(error);
      }
    },
    async propose(clientId, kind, reason) {
      try {
        return toLock(await http.request<LockJson>("POST", `/staff/clients/${encodeURIComponent(clientId)}/locks`, { kind, reason }));
      } catch (error) {
        throw toClientsError(error);
      }
    },
    async decide(lockId, decision, reason) {
      try {
        return toLock(await http.request<LockJson>("POST", `/staff/clients/locks/${encodeURIComponent(lockId)}/decision`, { decision, reason }));
      } catch (error) {
        throw toClientsError(error, true);
      }
    },
  };
}
