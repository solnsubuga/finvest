import { Bank, Limits, NameCheck, NewAccount, Payout, PayoutAccount, PayoutsClient, PayoutsError } from "./types";

/**
 * In-memory payout accounts and payouts for the demo build and tests. It refuses what the
 * backend refuses (docs/payouts.md): a malformed number, the same account twice, more than
 * five awaiting their code, a wrong code, proof for a mobile money number, and a step the
 * account is not at. Staff approval never happens here, so a new account stays in review.
 * The demo code is 123456. Nothing here is real.
 */

export const DEMO_PAYOUT_CODE = "123456";
/** The demo payout that came back, so its "send it again" screen can be opened. */
export const RETURNED_DEMO_PAYOUT = "demo-payout-returned";
const HOUR = 60 * 60 * 1000;
const MAX_AWAITING_CODE = 5;
const MAX_WRONG_CODES = 5;
/** Lookups of the name a network holds, per number, as the backend allows. */
const MAX_NAME_CHECKS = 3;
/** The demo's mobile money limit per payout, as `getLimits` answers it. */
const MOBILE_MONEY_PER_PAYOUT = 5_000_000;
const NETWORKS: Record<string, "mtn" | "airtel"> = { "76": "mtn", "77": "mtn", "78": "mtn", "39": "mtn", "70": "airtel", "74": "airtel", "75": "airtel", "20": "airtel" };

/**
 * The demo's banks. The real list comes from the backend (GET /client/banks); this one only
 * has to hold the codes the demo accounts use and enough others to choose from.
 */
export const DEMO_BANKS: readonly Bank[] = [
  { code: "absa", name: "Absa Bank" },
  { code: "centenary", name: "Centenary Bank" },
  { code: "dfcu", name: "dfcu Bank" },
  { code: "equity", name: "Equity Bank" },
  { code: "housing_finance", name: "Housing Finance Bank" },
  { code: "stanbic", name: "Stanbic Bank" },
  { code: "standard_chartered", name: "Standard Chartered" },
];

type Stored = Omit<PayoutAccount, "bankName"> & {
  number: string;
  removed: boolean;
  wrongCodes: number;
  codeSent: boolean;
  nameChecks: number;
};

/** A demo Airtel number stuck at its name check, or refused for having no name. */
export type NameCheckSample = NameCheck | "no_name";

export type StubOptions = {
  /** "empty" is a new client with no accounts and no payouts. */
  sample?: "default" | "empty";
  /** Every read fails as if offline, to show the error states. */
  offline?: boolean;
  /** Adds an Airtel number at this point of its name check. */
  nameCheck?: NameCheckSample;
  now?: () => number;
  /** How long each call takes, so the loading states show. Tests use 0. */
  delayMs?: number;
};

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function seed(nowMs: number): Stored[] {
  const base = {
    proofKind: null,
    rejectionReason: null,
    nameCheck: null,
    firstPaidAt: null,
    removed: false,
    wrongCodes: 0,
    codeSent: false,
    nameChecks: 0,
  };
  // The first two match the investing demo's approved accounts, so a withdrawal there lands here.
  return [
    {
      ...base,
      id: "demo-account-bank",
      kind: "bank",
      network: null,
      bankCode: "stanbic",
      number: "9030012344821",
      account: "•••• 4821",
      holderName: "Amara Nakato",
      status: "approved",
      proofKind: "bank_statement",
      payableFrom: iso(nowMs + 20 * HOUR),
      createdAt: iso(nowMs - 30 * HOUR),
    },
    {
      ...base,
      id: "demo-account-mtn",
      kind: "mobile_money",
      network: "mtn",
      bankCode: null,
      number: "256772123456",
      account: "+256 77• ••• 456",
      holderName: "Amara Nakato",
      status: "approved",
      proofKind: "network_registered_name",
      payableFrom: iso(nowMs - 40 * 24 * HOUR),
      firstPaidAt: iso(nowMs - 12 * 24 * HOUR),
      createdAt: iso(nowMs - 42 * 24 * HOUR),
    },
    {
      ...base,
      id: "demo-account-centenary",
      kind: "bank",
      network: null,
      bankCode: "centenary",
      number: "3100045567190",
      account: "•••• 7190",
      holderName: "Amara Nakato",
      status: "rejected",
      proofKind: "bank_statement",
      rejectionReason: "proof_too_old",
      payableFrom: null,
      createdAt: iso(nowMs - 6 * 24 * HOUR),
    },
  ];
}

function seedNameCheck(sample: NameCheckSample, nowMs: number): Stored {
  const refused = sample === "no_name";
  return {
    id: "demo-account-airtel",
    kind: "mobile_money",
    network: "airtel",
    bankCode: null,
    number: "256751234789",
    account: "+256 75• ••• 789",
    holderName: "Amara Nakato",
    status: refused ? "rejected" : "awaiting_proof",
    proofKind: refused ? "network_registered_name" : null,
    rejectionReason: refused ? "network_name_unavailable" : null,
    nameCheck: refused ? null : sample,
    payableFrom: null,
    firstPaidAt: null,
    createdAt: iso(nowMs - 2 * HOUR),
    removed: false,
    wrongCodes: 0,
    codeSent: false,
    nameChecks: sample === "limit_reached" ? MAX_NAME_CHECKS : 1,
  };
}

function seedPayouts(nowMs: number): Payout[] {
  return [
    {
      id: "demo-payout-2",
      destinationId: "demo-account-mtn",
      kind: "mobile_money",
      amount: { currency: "UGX", minor: 512_400 },
      status: "processing",
      businessDate: iso(nowMs).slice(0, 10),
      createdAt: iso(nowMs - 2 * HOUR),
      returnedAt: null,
      resentAs: null,
    },
    {
      id: "demo-payout-1",
      destinationId: "demo-account-mtn",
      kind: "mobile_money",
      amount: { currency: "UGX", minor: 1_254_300 },
      status: "paid",
      businessDate: iso(nowMs - 12 * 24 * HOUR).slice(0, 10),
      createdAt: iso(nowMs - 12 * 24 * HOUR),
      returnedAt: null,
      resentAs: null,
    },
    {
      // Paid to a bank account since closed, and sent back by the bank. It waits to be sent again.
      id: RETURNED_DEMO_PAYOUT,
      destinationId: "demo-account-closed",
      kind: "bank",
      amount: { currency: "UGX", minor: 2_150_000 },
      status: "paid",
      businessDate: iso(nowMs - 20 * 24 * HOUR).slice(0, 10),
      createdAt: iso(nowMs - 20 * 24 * HOUR),
      returnedAt: iso(nowMs - 3 * 24 * HOUR),
      resentAs: null,
    },
  ];
}

/** Accepts 0772123456, 772123456, 256772123456 or +256 772 123 456, as the backend does. */
function parsePhone(typed: string): string {
  const trimmed = typed.trim();
  if (!/^\+?[0-9 ]{9,16}$/.test(trimmed)) throw new PayoutsError("phone_invalid");
  let digits = trimmed.replace(/[+ ]/g, "");
  if (digits.length === 10 && digits.startsWith("0")) digits = `256${digits.slice(1)}`;
  else if (digits.length === 9) digits = `256${digits}`;
  if (!/^256[0-9]{9}$/.test(digits)) throw new PayoutsError("phone_invalid");
  if (!NETWORKS[digits.slice(3, 5)]) throw new PayoutsError("phone_network_unsupported");
  return digits;
}

function view(account: Stored): PayoutAccount {
  const { number: _number, removed: _removed, wrongCodes: _wrong, codeSent: _sent, nameChecks: _checks, ...shown } = account;
  return { ...shown, bankName: DEMO_BANKS.find((bank) => bank.code === account.bankCode)?.name ?? null };
}

export function createStubPayoutsClient(options: StubOptions = {}): PayoutsClient {
  const now = options.now ?? (() => Date.now());
  const accounts: Stored[] = options.sample === "empty" ? [] : seed(now());
  if (options.nameCheck) accounts.push(seedNameCheck(options.nameCheck, now()));
  const payouts: Payout[] = options.sample === "empty" ? [] : seedPayouts(now());
  let next = 1;
  // The payout each one that came back was sent again as. Sent again once only.
  const resent = new Map<string, Payout>();

  const delayMs = options.delayMs ?? 250;
  const pause = () => (delayMs > 0 ? new Promise((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve());
  async function reachable(): Promise<void> {
    await pause();
    if (options.offline) throw new PayoutsError("unavailable");
  }
  function find(accountId: string): Stored {
    const account = accounts.find((candidate) => candidate.id === accountId && !candidate.removed);
    if (!account) throw new PayoutsError("not_found");
    return account;
  }

  return {
    async listBanks() {
      await reachable();
      return [...DEMO_BANKS];
    },

    async listAccounts() {
      await reachable();
      return accounts
        .filter((account) => !account.removed)
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
        .map(view);
    },

    async getAccount(accountId) {
      await reachable();
      return view(find(accountId));
    },

    async addAccount(account: NewAccount) {
      await reachable();
      const holderName = account.holderName.trim();
      let number: string;
      if (account.kind === "mobile_money") {
        number = parsePhone(account.phone);
      } else {
        if (!DEMO_BANKS.some((bank) => bank.code === account.bankCode)) throw new PayoutsError("bank_invalid");
        number = account.accountNumber.trim().replace(/[ -]/g, "");
        if (!/^[0-9]{6,20}$/.test(number)) throw new PayoutsError("account_number_invalid");
      }
      if (!holderName || holderName.length > 120) throw new PayoutsError("holder_name_invalid");
      const live = accounts.filter((saved) => !saved.removed);
      if (live.filter((saved) => saved.status === "awaiting_code").length >= MAX_AWAITING_CODE) {
        throw new PayoutsError("too_many_unconfirmed");
      }
      if (live.some((saved) => saved.kind === account.kind && saved.number === number && saved.bankCode === (account.kind === "bank" ? account.bankCode : null))) {
        throw new PayoutsError("already_saved");
      }
      const created: Stored = {
        id: `demo-account-new-${next++}`,
        kind: account.kind,
        network: account.kind === "mobile_money" ? (NETWORKS[number.slice(3, 5)] ?? null) : null,
        bankCode: account.kind === "bank" ? account.bankCode : null,
        number,
        account: account.kind === "mobile_money" ? `+256 ${number.slice(3, 5)}• ••• ${number.slice(-3)}` : `•••• ${number.slice(-4)}`,
        holderName,
        status: "awaiting_code",
        proofKind: null,
        rejectionReason: null,
        nameCheck: null,
        payableFrom: null,
        firstPaidAt: null,
        createdAt: iso(now()),
        removed: false,
        wrongCodes: 0,
        codeSent: false,
        nameChecks: 0,
      };
      accounts.push(created);
      return view(created);
    },

    async sendCode(accountId) {
      await reachable();
      const account = find(accountId);
      if (account.status !== "awaiting_code") throw new PayoutsError("moved_on");
      account.codeSent = true;
      account.wrongCodes = 0;
    },

    async verifyCode(accountId, code) {
      await reachable();
      const account = find(accountId);
      if (account.status !== "awaiting_code") throw new PayoutsError("moved_on");
      if (!account.codeSent || account.wrongCodes >= MAX_WRONG_CODES) throw new PayoutsError("code_expired");
      if (code !== DEMO_PAYOUT_CODE) {
        account.wrongCodes += 1;
        throw new PayoutsError("invalid_code");
      }
      account.status = "awaiting_proof";
      account.codeSent = false;
      if (account.kind === "mobile_money") {
        // The backend asks the network for the name in the same step. The demo never answers.
        account.nameCheck = "checking";
        account.nameChecks = 1;
      }
      return view(account);
    },

    async checkNameAgain(accountId) {
      await reachable();
      const account = find(accountId);
      if (account.kind !== "mobile_money" || account.status !== "awaiting_proof") throw new PayoutsError("moved_on");
      if (account.nameCheck === "checking") throw new PayoutsError("name_check_in_progress");
      if (account.nameCheck === "waiting_for_id") {
        // The demo client's ID is approved, so the same answer is compared now and matches.
        account.status = "in_review";
        account.proofKind = "network_registered_name";
        account.nameCheck = null;
        return view(account);
      }
      if (account.nameChecks >= MAX_NAME_CHECKS) throw new PayoutsError("name_check_limit_reached");
      account.nameChecks += 1;
      account.nameCheck = "checking";
      return view(account);
    },

    async uploadProof(accountId, proofKind) {
      await reachable();
      const account = find(accountId);
      if (account.kind !== "bank") throw new PayoutsError("moved_on");
      const final = account.rejectionReason === "name_different" || account.rejectionReason === "suspected_fraud";
      if (!(account.status === "awaiting_proof" || (account.status === "rejected" && !final))) {
        throw new PayoutsError("moved_on");
      }
      account.status = "in_review";
      account.proofKind = proofKind;
      account.rejectionReason = null;
      return view(account);
    },

    async removeAccount(accountId) {
      await reachable();
      find(accountId).removed = true;
    },

    async listPayouts() {
      await reachable();
      return payouts.map((payout) => ({ ...payout }));
    },

    async getPayout(payoutId) {
      await reachable();
      const payout = payouts.find((candidate) => candidate.id === payoutId);
      if (!payout) throw new PayoutsError("not_found");
      return { ...payout };
    },

    async resend(payoutId, destinationId) {
      await reachable();
      const payout = payouts.find((candidate) => candidate.id === payoutId);
      if (!payout) throw new PayoutsError("not_found");
      if (!payout.returnedAt) throw new PayoutsError("not_returned");
      const before = resent.get(payoutId);
      if (before) {
        if (before.destinationId !== destinationId) throw new PayoutsError("already_resent");
        return { ...before };
      }
      const account = accounts.find((candidate) => candidate.id === destinationId && !candidate.removed);
      if (!account || account.status !== "approved" || !account.payableFrom || Date.parse(account.payableFrom) > now()) {
        throw new PayoutsError("account_not_ready");
      }
      if (account.kind === "mobile_money" && payout.amount.minor > MOBILE_MONEY_PER_PAYOUT) {
        throw new PayoutsError("over_mobile_money_limit");
      }
      const created: Payout = {
        id: `demo-payout-resent-${next++}`,
        destinationId,
        kind: account.kind,
        amount: { ...payout.amount },
        status: "processing",
        businessDate: iso(now()).slice(0, 10),
        createdAt: iso(now()),
        returnedAt: null,
      resentAs: null,
      };
      payouts.unshift(created);
      payout.resentAs = created.id;
      resent.set(payoutId, created);
      return { ...created };
    },

    async getLimits(): Promise<Limits> {
      await reachable();
      return {
        effectiveFrom: "2026-11-01",
        mobileMoneyPerPayout: { currency: "UGX", minor: MOBILE_MONEY_PER_PAYOUT },
        mobileMoneyPerDay: null,
        bankPerPayout: null,
        bankPerDay: null,
      };
    },
  };
}
