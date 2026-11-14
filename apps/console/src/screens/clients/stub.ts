/**
 * Sample clients for `pnpm dev` and preview builds. Nothing here is real: the names, numbers
 * and amounts are made up. The rules follow the API: two people for every lock and unlock.
 */

import { ClientsError, type Account, type ClientPage, type ClientsApi, type Lock } from "./api.ts";

interface Me {
  id: string;
  name: string;
}

const wait = () => new Promise((resolve) => setTimeout(resolve, 150));
const copy = <T>(value: T): T => structuredClone(value);

function samples(now: number): ClientPage[] {
  const ago = (hours: number) => new Date(now - hours * 60 * 60 * 1000).toISOString();
  const account = (id: string, phone: string, email: string | null, status: Account["status"], created: string, extra: Partial<Account> = {}): Account => ({
    id,
    phone,
    email,
    status,
    createdAt: created,
    restrictedUntil: null,
    hasPasskey: true,
    hasAuthenticator: false,
    lastSignedInAt: ago(5),
    ...extra,
  });
  const proposedByGrace: Lock = {
    id: "-NstubLock0000000003",
    kind: "lock",
    reason: "Client called from a new number to say their phone was stolen last night.",
    proposedByName: "Grace Auma",
    proposedAt: ago(1),
    status: "proposed",
    decidedByName: null,
    decidedAt: null,
    decisionReason: null,
    yours: false,
  };
  return [
    {
      account: account("-NstubClient00000042", "+256772123456", "sarah.n@example.com", "active", "2026-08-03T07:12:00Z", { hasAuthenticator: true }),
      verifiedName: "Sarah Nakato",
      identityCheck: { id: "-NstubCheck000000042", status: "approved", submittedAt: "2026-08-03T07:40:00Z", decidedAt: "2026-08-03T11:05:00Z", accountNumber: "1004382917" },
      owed: [
        { accountCode: "client.holding", accountReference: "TB364-0042", minor: 2_500_000, currency: "UGX" },
        { accountCode: "client.interest_accrued", accountReference: "TB364-0042", minor: 61_438, currency: "UGX" },
        { accountCode: "client.awaiting_start", accountReference: null, minor: 500_000, currency: "UGX" },
      ],
      destinations: [
        {
          id: "-NstubDest0000000042",
          kind: "mobile_money",
          network: "mtn",
          bankCode: null,
          maskedAccount: "•••• 3456",
          holderName: "Sarah Nakato",
          status: "approved",
          createdAt: "2026-08-04T09:00:00Z",
        },
      ],
      locks: [proposedByGrace],
      freezes: [],
      conversation: { id: "-NstubChat0000000042", topic: "Payout timing", status: "open", lastMessageAt: ago(2), waitingSince: ago(2) },
      complaints: [
        {
          id: "-NstubComplaint00042",
          reference: "FC-2026-0118",
          category: "payouts",
          summary: "Payout arrived a day after the date shown",
          status: "investigating",
          receivedAt: "2026-11-10T08:30:00Z",
          dueOn: "2026-11-24",
          overdue: false,
        },
      ],
      canLock: true,
    },
    {
      account: account("-NstubClient00000017", "+256701987654", "j.okot@example.com", "locked", "2026-09-21T10:00:00Z", { lastSignedInAt: ago(72) }),
      verifiedName: "James Okot",
      identityCheck: { id: "-NstubCheck000000017", status: "approved", submittedAt: "2026-09-21T10:20:00Z", decidedAt: "2026-09-22T08:00:00Z", accountNumber: "1007719354" },
      owed: [{ accountCode: "client.holding", accountReference: "FD12-0017", minor: 10_000_000, currency: "UGX" }],
      destinations: [
        {
          id: "-NstubDest0000000017",
          kind: "bank",
          network: null,
          bankCode: "stanbic",
          maskedAccount: "•••• 0021",
          holderName: "James Okot",
          status: "approved",
          createdAt: "2026-09-23T09:00:00Z",
        },
      ],
      locks: [
        {
          id: "-NstubLock0000000002",
          kind: "lock",
          reason: "Sign-ins from two countries within an hour. Locked until the client confirms on a call.",
          proposedByName: "Daniel Kato",
          proposedAt: ago(70),
          status: "approved",
          decidedByName: "Peter Okello",
          decidedAt: ago(69),
          decisionReason: "Agreed. Pattern matches account takeover.",
          yours: false,
        },
      ],
      freezes: [],
      conversation: null,
      complaints: [],
      canLock: true,
    },
    {
      account: account("-NstubClient00000077", "+256782555077", "p.mukasa@example.com", "active", "2026-08-19T09:30:00Z", { lastSignedInAt: ago(30) }),
      verifiedName: "Peter Mukasa",
      identityCheck: { id: "-NstubCheck000000077", status: "approved", submittedAt: "2026-08-19T09:50:00Z", decidedAt: "2026-08-19T13:00:00Z", accountNumber: "1004771205" },
      owed: [],
      destinations: [],
      locks: [],
      freezes: [],
      conversation: null,
      complaints: [],
      canLock: true,
    },
    {
      account: account("-NstubClient00000063", "+256704630063", null, "active", "2026-09-02T12:00:00Z", { lastSignedInAt: ago(3) }),
      verifiedName: "Ruth Akello",
      identityCheck: { id: "-NstubCheck000000063", status: "approved", submittedAt: "2026-09-02T12:20:00Z", decidedAt: "2026-09-02T15:10:00Z", accountNumber: "1004630638" },
      owed: [],
      destinations: [],
      locks: [],
      freezes: [],
      conversation: null,
      complaints: [],
      canLock: true,
    },
    {
      account: account("-NstubClient00000058", "+256773580058", "a.namuli@example.com", "locked", "2026-07-14T08:00:00Z", { lastSignedInAt: ago(4) }),
      verifiedName: "Annet Namuli",
      identityCheck: { id: "-NstubCheck000000058", status: "approved", submittedAt: "2026-07-14T08:30:00Z", decidedAt: "2026-07-14T12:00:00Z", accountNumber: "1005880412" },
      owed: [{ accountCode: "client.holding", accountReference: "TB182-0058", minor: 4_000_000, currency: "UGX" }],
      destinations: [],
      locks: [],
      freezes: [{ id: "-NstubFreeze00000058", frozenAt: ago(3), reason: "Lost my phone on a boda this morning" }],
      conversation: null,
      complaints: [],
      canLock: true,
    },
    {
      account: account("-NstubClient00000099", "+256752000111", null, "unclaimed", "2026-11-01T06:00:00Z", { hasPasskey: false, lastSignedInAt: null }),
      verifiedName: null,
      identityCheck: null,
      owed: [{ accountCode: "client.holding", accountReference: "TB182-0099", minor: 1_200_000, currency: "UGX" }],
      destinations: [],
      locks: [],
      freezes: [],
      conversation: null,
      complaints: [],
      canLock: true,
    },
  ];
}

function digits(value: string): string {
  const only = value.replace(/\D/g, "");
  return only.startsWith("0") ? `256${only.slice(1)}` : only;
}

/** What the reader's role may see and do, as the API decides it from their permissions. */
export interface StubAccess {
  canLock: boolean;
  seesSupport: boolean;
  seesComplaints: boolean;
}

const EVERYTHING: StubAccess = { canLock: true, seesSupport: true, seesComplaints: true };

export function createStubClientsApi(
  me: Me,
  now: () => number = Date.now,
  access: StubAccess = EVERYTHING,
): ClientsApi & { closeAccount(id: string): void } {
  const pages = samples(now());
  let next = 10;
  const find = (id: string) => {
    const page = pages.find((item) => item.account.id === id);
    if (!page) throw new ClientsError("not_found");
    return page;
  };
  const check = (page: ClientPage, kind: Lock["kind"]) => {
    const wanted = kind === "lock" ? "active" : "locked";
    if (page.account.status === wanted) return;
    if (page.account.status === "closed") throw new ClientsError("closed");
    throw new ClientsError(page.account.status === "locked" ? "already_locked" : "not_locked");
  };

  return {
    /** The closures stub calls this once a closure is approved, as the API closes the account. */
    closeAccount(id) {
      const page = pages.find((item) => item.account.id === id);
      if (page) page.account.status = "closed";
    },
    async search(query) {
      await wait();
      const text = query.trim();
      const number = text.replace(/[\s-]/g, "");
      const found = pages.find(
        (page) =>
          page.account.id === text ||
          (number.length === 10 && page.identityCheck?.accountNumber === number) ||
          (digits(text).length >= 9 && digits(page.account.phone) === digits(text)),
      );
      return found ? [copy(found.account)] : [];
    },
    async client(id) {
      await wait();
      const page = copy(find(id));
      return {
        ...page,
        conversation: access.seesSupport ? page.conversation : null,
        complaints: access.seesComplaints ? page.complaints : null,
        canLock: access.canLock,
      };
    },
    async propose(clientId, kind, reason) {
      await wait();
      if (!access.canLock) throw new ClientsError("forbidden");
      const page = find(clientId);
      if (!reason.trim()) throw new ClientsError("invalid");
      check(page, kind);
      if (page.locks.some((lock) => lock.status === "proposed")) throw new ClientsError("waiting");
      const lock: Lock = {
        id: `-NstubLock00000000${String(next++).padStart(2, "0")}`,
        kind,
        reason: reason.trim(),
        proposedByName: me.name,
        proposedAt: new Date(now()).toISOString(),
        status: "proposed",
        decidedByName: null,
        decidedAt: null,
        decisionReason: null,
        yours: true,
      };
      page.locks.unshift(lock);
      return copy(lock);
    },
    async decide(lockId, decision, reason) {
      await wait();
      if (!access.canLock) throw new ClientsError("forbidden");
      const page = pages.find((item) => item.locks.some((lock) => lock.id === lockId && lock.status === "proposed"));
      const lock = page?.locks.find((item) => item.id === lockId);
      if (!page || !lock) throw new ClientsError("not_found");
      if (!reason.trim()) throw new ClientsError("invalid");
      if (lock.yours !== (decision === "withdrawn")) throw new ClientsError("same_person");
      if (decision === "approved") {
        check(page, lock.kind);
        page.account.status = lock.kind === "lock" ? "locked" : "active";
      }
      Object.assign(lock, { status: decision, decidedByName: me.name, decidedAt: new Date(now()).toISOString(), decisionReason: reason.trim() });
      return copy(lock);
    },
  };
}
