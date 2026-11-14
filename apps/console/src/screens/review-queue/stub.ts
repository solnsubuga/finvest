/**
 * A sample review queue for `pnpm dev` and preview builds. Not real people or accounts.
 *
 * It refuses what the backend refuses: a name that is not the client's, a statement with no
 * date or older than three months, a check for a client with no approved identity check,
 * approving your own check or an account approved for someone else, and deciding a payout that
 * is no longer held. A decision key reused for other details is refused as `key_reused`.
 */

import {
  ReviewQueueError,
  type DecidedPayout,
  type HeldPayout,
  type NameMatch,
  type PayoutAccount,
  type PayoutAccountDetail,
  type ReviewQueueApi,
} from "./api.ts";

export type StubCase = "sample" | "empty" | "error";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

function words(name: string): string[] {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

/** The backend's name lock, near enough for samples: order, case, accents and punctuation ignored. */
export function compareNames(onProof: string, onId: string): NameMatch | "different" {
  const proof = new Set(words(onProof));
  const id = new Set(words(onId));
  const shared = [...proof].filter((word) => id.has(word)).length;
  if (shared === proof.size && shared === id.size) return "exact";
  if (shared >= 2 && (shared === proof.size || shared === id.size)) return "partial";
  return "different";
}

function day(at: number): string {
  return new Date(at).toISOString().slice(0, 10);
}

function sampleAccounts(me: string, now: number): PayoutAccountDetail[] {
  const base = {
    status: "in_review" as const,
    rejectionReason: null,
    approvedBy: null,
    approvedAt: null,
    payableFrom: null,
  };
  return [
    {
      ...base,
      id: "sample-account-amara",
      clientId: "sample-client-amara",
      kind: "mobile_money",
      network: "mtn",
      bankCode: null,
      account: "+256 77• ••• 456",
      accountNumber: "+256 772 418 456",
      holderName: "Amara Nakato",
      idName: "NAKATO AMARA",
      proofKind: "network_registered_name",
      proofLink: null,
      check: null,
      createdAt: new Date(now - 3 * HOUR).toISOString(),
    },
    {
      ...base,
      id: "sample-account-peter",
      clientId: "sample-client-peter",
      kind: "bank",
      network: null,
      bankCode: "stanbic",
      account: "•••• 4821",
      accountNumber: "9030012744821",
      holderName: "Peter Okello",
      idName: "OKELLO PETER JAMES",
      proofKind: "bank_statement",
      proofLink: { url: "#sample-proof", expiresAt: new Date(now + 5 * 60_000).toISOString() },
      check: {
        proofName: "Peter Okello",
        nameMatch: "partial",
        statementDate: day(now - 12 * DAY),
        checkedBy: "sample-staff-grace",
        checkedAt: new Date(now - 40 * 60_000).toISOString(),
      },
      createdAt: new Date(now - 26 * HOUR).toISOString(),
    },
    {
      ...base,
      id: "sample-account-joan",
      clientId: "sample-client-joan",
      kind: "bank",
      network: null,
      bankCode: "centenary_bank",
      account: "•••• 0193",
      accountNumber: "3100044870193",
      holderName: "Joan Namutebi",
      idName: "NAMUTEBI JOAN",
      proofKind: "bank_card",
      proofLink: { url: "#sample-proof", expiresAt: new Date(now + 5 * 60_000).toISOString() },
      check: {
        proofName: "Joan Namutebi",
        nameMatch: "exact",
        statementDate: null,
        checkedBy: me,
        checkedAt: new Date(now - 15 * 60_000).toISOString(),
      },
      createdAt: new Date(now - 30 * HOUR).toISOString(),
    },
    {
      ...base,
      id: "sample-account-sarah",
      clientId: "sample-client-sarah",
      kind: "bank",
      network: null,
      bankCode: "dfcu",
      account: "•••• 7710",
      accountNumber: "01203500777710",
      holderName: "Sarah Achieng",
      idName: "ACHIENG SARAH",
      proofKind: "bank_letter",
      proofLink: { url: "#sample-proof", expiresAt: new Date(now + 5 * 60_000).toISOString() },
      check: {
        proofName: "Sarah Achieng",
        nameMatch: "exact",
        statementDate: null,
        checkedBy: "sample-staff-daniel",
        checkedAt: new Date(now - 2 * HOUR).toISOString(),
      },
      createdAt: new Date(now - 2 * DAY).toISOString(),
    },
    {
      ...base,
      id: "sample-account-david",
      clientId: "sample-client-david",
      kind: "mobile_money",
      network: "airtel",
      bankCode: null,
      account: "+256 75• ••• 902",
      accountNumber: "+256 752 610 902",
      holderName: "David Mugisha",
      idName: null,
      proofKind: "network_registered_name",
      proofLink: null,
      check: null,
      createdAt: new Date(now - 50 * 60_000).toISOString(),
    },
  ];
}

function sampleHeld(now: number): HeldPayout[] {
  return [
    {
      id: "sample-payout-ivan",
      clientId: "sample-client-ivan",
      destinationId: "sample-destination-ivan",
      kind: "bank",
      amount: { minor: 4_000_000, currency: "USD" },
      businessDate: day(now - DAY),
      createdAt: new Date(now - 27 * HOUR).toISOString(),
    },
    {
      id: "sample-payout-joan",
      clientId: "sample-client-joan",
      destinationId: "sample-destination-joan",
      kind: "bank",
      amount: { minor: 25_000_000, currency: "UGX" },
      businessDate: day(now),
      createdAt: new Date(now - 5 * HOUR).toISOString(),
    },
    {
      id: "sample-payout-kira",
      clientId: "sample-client-kira",
      destinationId: "sample-destination-kira",
      kind: "mobile_money",
      amount: { minor: 12_500_000, currency: "UGX" },
      businessDate: day(now),
      createdAt: new Date(now - 2 * HOUR).toISOString(),
    },
  ];
}

/** The account as the queue lists it: no whole number, ID name or proof link. */
function listed(account: PayoutAccountDetail): PayoutAccount {
  return {
    id: account.id,
    clientId: account.clientId,
    kind: account.kind,
    network: account.network,
    bankCode: account.bankCode,
    account: account.account,
    holderName: account.holderName,
    status: account.status,
    proofKind: account.proofKind,
    check: account.check ? { ...account.check } : null,
    rejectionReason: account.rejectionReason,
    approvedBy: account.approvedBy,
    approvedAt: account.approvedAt,
    payableFrom: account.payableFrom,
    createdAt: account.createdAt,
  };
}

export function createStubReviewQueueApi(me: string, clock: () => number = Date.now, sample: StubCase = "sample"): ReviewQueueApi {
  if (sample === "error") {
    const failure = () => Promise.reject(new ReviewQueueError("unavailable"));
    return {
      listAccounts: failure,
      openAccount: failure,
      check: failure,
      approve: failure,
      reject: failure,
      remove: failure,
      listHeld: failure,
      decideHeld: failure,
    };
  }
  const accounts = sample === "empty" ? [] : sampleAccounts(me, clock());
  const held = sample === "empty" ? [] : sampleHeld(clock());
  // Answers already given, by key, as the backend's idempotency keys do.
  const decided = new Map<string, { request: string; answer: DecidedPayout }>();

  function find(id: string): PayoutAccountDetail {
    const account = accounts.find((item) => item.id === id);
    if (!account) throw new ReviewQueueError("not_found");
    return account;
  }

  function inReview(id: string): PayoutAccountDetail {
    const account = find(id);
    if (account.status !== "in_review") throw new ReviewQueueError("conflict");
    return account;
  }

  const answer = <T>(run: () => T): Promise<T> => {
    try {
      return Promise.resolve(run());
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new ReviewQueueError("unavailable"));
    }
  };

  return {
    listAccounts: () =>
      answer(() =>
        accounts
          .filter((account) => account.status === "in_review")
          .sort((a, b) => a.createdAt.localeCompare(b.createdAt))
          .map(listed),
      ),
    openAccount: (id) =>
      answer(() => {
        const account = find(id);
        return { ...account, check: account.check ? { ...account.check } : null };
      }),
    check: (id, check) =>
      answer(() => {
        const account = inReview(id);
        if (account.idName === null) throw new ReviewQueueError("client_not_verified");
        const match = compareNames(check.proofName, account.idName);
        if (match === "different") throw new ReviewQueueError("name_different");
        let statementDate: string | null = null;
        if (account.proofKind === "bank_statement") {
          if (!check.statementDate) throw new ReviewQueueError("statement_date_required");
          const oldest = new Date(clock());
          oldest.setUTCMonth(oldest.getUTCMonth() - 3);
          if (check.statementDate < day(oldest.getTime())) throw new ReviewQueueError("statement_too_old");
          statementDate = check.statementDate;
        }
        account.check = {
          proofName: check.proofName,
          nameMatch: match,
          statementDate,
          checkedBy: me,
          checkedAt: new Date(clock()).toISOString(),
        };
        return listed(account);
      }),
    approve: (id) =>
      answer(() => {
        const account = inReview(id);
        if (!account.check) throw new ReviewQueueError("destination_not_checked");
        if (account.check.checkedBy === me) throw new ReviewQueueError("checked_by_you");
        if (account.id === "sample-account-sarah") throw new ReviewQueueError("account_in_use");
        const at = clock();
        account.status = "approved";
        account.approvedBy = me;
        account.approvedAt = new Date(at).toISOString();
        account.payableFrom = new Date(at + DAY).toISOString();
        return listed(account);
      }),
    reject: (id, rejectionReason) =>
      answer(() => {
        const account = inReview(id);
        account.status = "rejected";
        account.rejectionReason = rejectionReason;
        account.check = null;
        return listed(account);
      }),
    remove: (id) =>
      answer(() => {
        const account = find(id);
        if (account.status === "removed") throw new ReviewQueueError("conflict");
        account.status = "removed";
        account.check = null;
        return listed(account);
      }),
    listHeld: () => answer(() => held.map((payout) => ({ ...payout, amount: { ...payout.amount } }))),
    decideHeld: (id, decision, reason, key) =>
      answer(() => {
        const request = JSON.stringify({ id, decision, reason });
        const known = decided.get(key);
        if (known) {
          if (known.request !== request) throw new ReviewQueueError("key_reused");
          return { ...known.answer };
        }
        const at = held.findIndex((payout) => payout.id === id);
        if (at < 0) throw new ReviewQueueError("payout_not_held");
        held.splice(at, 1);
        const result: DecidedPayout =
          decision === "approve" ? { id, decision: "approved", status: "queued" } : { id, decision: "declined", status: "cancelled" };
        decided.set(key, { request, answer: result });
        return { ...result };
      }),
  };
}
