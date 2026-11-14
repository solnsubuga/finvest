import { demoClosing } from "@/demo/closing";
import { addDemoPayable, demoPaidAt } from "@/demo/payables";

import {
  Amount,
  Investment,
  InvestingClient,
  InvestingError,
  NewInvestment,
  PayoutAccount,
  Product,
  Quote,
} from "./types";

/**
 * An in-memory stand-in for the investing API, so the screens can be built and
 * tried before the routes are wired in. Nothing here is real: no money, and
 * everything is forgotten when the app reloads. `resolveInvesting.ts` keeps it
 * out of production builds.
 *
 * It works interest out the way the backend does, in whole shillings rounded
 * down, so the demo figures match: UGX 1,000,000 for 91 days at 12.5% before
 * 15% tax earns UGX 26,490. The screens never do this sum themselves.
 */

type Terms = { rateBps: number; taxBps: number };

const PRODUCTS: (Product & Terms)[] = [
  {
    code: "TREA-91",
    name: "91 Day Treasury Bill",
    kind: "treasury_bill",
    currency: "UGX",
    termDays: 91,
    minimum: { currency: "UGX", minor: 100_000 },
    interestPaid: "at_maturity",
    status: "live",
    ratePercent: "10.63",
    rateBps: 1250,
    taxBps: 1500,
  },
  {
    code: "TREA-182",
    name: "182 Day Treasury Bill",
    kind: "treasury_bill",
    currency: "UGX",
    termDays: 182,
    minimum: { currency: "UGX", minor: 200_000 },
    interestPaid: "at_maturity",
    status: "live",
    ratePercent: "11.69",
    rateBps: 1375,
    taxBps: 1500,
  },
  {
    code: "USD-BOND",
    name: "USD Fixed Income",
    kind: "fixed_income",
    currency: "USD",
    termDays: null,
    minimum: { currency: "USD", minor: 500_00 },
    interestPaid: "monthly",
    status: "paused",
    ratePercent: "6.89",
    rateBps: 725,
    taxBps: 500,
  },
];

const DAY_MS = 24 * 60 * 60 * 1000;
/** Uganda keeps UTC+3 all year. */
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;
const PAY_WINDOW_MS = 72 * 60 * 60 * 1000;
const MAX_UNPAID = 3;

function kampalaDay(ms: number): string {
  return new Date(ms + KAMPALA_OFFSET_MS).toISOString().slice(0, 10);
}

function addDays(isoDate: string, days: number): string {
  return new Date(Date.parse(`${isoDate}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS);
}

/** Net interest for a number of days, rounded down twice as the backend rounds it. */
function netInterest(principal: number, terms: Terms, days: number): number {
  const gross = Math.floor((principal * terms.rateBps * days) / (10_000 * 365));
  const tax = Math.floor((gross * terms.taxBps) / 10_000);
  return gross - tax;
}

type Stored = {
  id: string;
  product: Product & Terms;
  amount: Amount;
  atMaturity: Investment["atMaturity"];
  /** The account chosen to be paid at maturity. */
  destinationId: string | null;
  /** Set once a matured investment's money was sent. */
  payoutId: string | null;
  createdAtMs: number;
  /** Set for the sample investments, which start already running. */
  startDate: string | null;
  /** When the client asked to withdraw it early, in milliseconds. */
  withdrawnAtMs: number | null;
};

/** The most mobile money takes in one payout, as the launch limits set it. */
const MOBILE_MONEY_PER_PAYOUT = 5_000_000;

/** Demo accounts: one ready, and one approved today, so still in its safety wait. */
function demoAccounts(nowMs: number): PayoutAccount[] {
  return [
    {
      id: "demo-account-mtn",
      kind: "mobile_money",
      label: "MTN +256 77• ••• 456",
      payableFrom: new Date(nowMs - 10 * DAY_MS).toISOString(),
      ready: true,
    },
    {
      id: "demo-account-bank",
      kind: "bank",
      label: "Stanbic Bank •••• 4821",
      payableFrom: new Date(nowMs + DAY_MS / 2).toISOString(),
      ready: false,
    },
  ];
}

/**
 * Shared by every demo client in the app's lifetime, so two never hand out the same ID: the
 * demo payments remember payments by ID.
 */
let nextId = 0;

export type StubOptions = {
  /** Artificial delay so loading states are visible. Tests pass 0. */
  delayMs?: number;
  /** The clock, in milliseconds. */
  now?: () => number;
  /** "empty" starts with no investments, as a new client does. */
  sample?: "default" | "empty";
  /** "none" has no approved accounts, to show that state. */
  accounts?: "default" | "none";
  /** True loses the answer to the first withdrawal request, to show that state. */
  loseFirstWithdrawal?: boolean;
  /** True adds an investment that matured to be paid out with no account chosen, to show that state. */
  maturedWaiting?: boolean;
};

export function createStubInvestingClient(options: StubOptions = {}): InvestingClient {
  const delayMs = options.delayMs ?? 350;
  const now = options.now ?? Date.now;
  const stored = new Map<string, Stored>();
  const byKey = new Map<string, { id: string; request: string }>();
  const withdrawalKeys = new Map<string, string>();
  let loseNextWithdrawal = options.loseFirstWithdrawal ?? false;

  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  const find = (code: string) => PRODUCTS.find((product) => product.code === code);

  if (options.sample !== "empty") {
    const today = kampalaDay(now());
    const seed = (
      code: string,
      minor: number,
      startedDaysAgo: number,
      atMaturity: Stored["atMaturity"],
      destinationId: string | null = null,
    ) => {
      const product = find(code) as Product & Terms;
      nextId += 1;
      const id = `demo-investment-${nextId}`;
      stored.set(id, {
        id,
        product,
        amount: { currency: product.currency, minor },
        atMaturity,
        destinationId,
        payoutId: null,
        createdAtMs: now() - (startedDaysAgo + 1) * DAY_MS,
        startDate: addDays(today, -startedDaysAgo),
        withdrawnAtMs: null,
      });
    };
    seed("TREA-91", 5_000_000, 85, "roll_over");
    seed("TREA-182", 2_000_000, 40, "pay_out", "demo-account-mtn");
    if (options.maturedWaiting) {
      // Its account was removed before it matured, so the money waits for the client to choose.
      seed("TREA-91", 3_000_000, 93, "pay_out");
    }
  }

  function startDate(entry: Stored): string | null {
    if (entry.startDate) {
      return entry.startDate;
    }
    const paidAt = demoPaidAt(entry.id);
    return paidAt ? kampalaDay(Date.parse(paidAt)) : null;
  }

  function view(entry: Stored): Investment {
    const { product, amount } = entry;
    const today = kampalaDay(now());
    const start = startDate(entry);
    const maturity = start && product.termDays !== null ? addDays(start, product.termDays) : null;
    const lapsed = !start && now() > entry.createdAtMs + PAY_WINDOW_MS;
    const matured = maturity !== null && today >= maturity;
    // Like the API: asking to withdraw early leaves it `active` with the request recorded. The
    // backend's worker moves it to `withdrawn` later; the demo never does, so both read alike.
    const status: Investment["status"] = lapsed
        ? "cancelled"
        : !start
          ? "awaiting_payment"
          : matured
            ? "matured"
            : "active";
    // Interest is earned from the day after the start, up to today or the maturity date. It
    // stops on the day an early withdrawal was asked for.
    const stopped = entry.withdrawnAtMs !== null ? kampalaDay(entry.withdrawnAtMs) : today;
    const lastDay = maturity && maturity < stopped ? maturity : stopped;
    const days = start ? Math.max(0, daysBetween(start, lastDay)) : 0;
    const soFar = netInterest(amount.minor, product, days);
    const yesterday = days > 0 ? netInterest(amount.minor, product, days - 1) : 0;
    const atMaturityInterest = product.termDays === null ? null : netInterest(amount.minor, product, product.termDays);
    const money = (minor: number): Amount => ({ currency: amount.currency, minor });
    return {
      id: entry.id,
      productCode: product.code,
      productName: product.name,
      status,
      amount,
      ratePercent: product.ratePercent,
      termDays: product.termDays,
      atMaturity: entry.atMaturity,
      payoutDestinationId: entry.atMaturity === "pay_out" ? entry.destinationId : null,
      canChangeMaturity: product.termDays !== null && (status === "awaiting_payment" || (status === "active" && maturity !== null && today < addDays(maturity, -1))),
      payBy: new Date(entry.createdAtMs + PAY_WINDOW_MS).toISOString(),
      startDate: start,
      maturityDate: maturity,
      daysEarned: days,
      interestSoFar: money(soFar),
      interestToday: money(status === "active" ? soFar - yesterday : 0),
      value: money(amount.minor + soFar),
      interestAtMaturity: atMaturityInterest === null ? null : money(atMaturityInterest),
      totalAtMaturity: atMaturityInterest === null ? null : money(amount.minor + atMaturityInterest),
      rolledIntoId: null,
      payoutId: entry.payoutId,
      waitingForDestination: status === "matured" && entry.atMaturity === "pay_out" && entry.payoutId === null && entry.destinationId === null,
      canWithdraw: status === "active" && entry.withdrawnAtMs === null && (maturity === null || today < maturity),
      withdrawalRequestedAt: entry.withdrawnAtMs === null ? null : new Date(entry.withdrawnAtMs).toISOString(),
    };
  }

  function check(productCode: string, amount: Amount): Product & Terms {
    const product = find(productCode);
    if (!product) {
      throw new InvestingError("not_found");
    }
    if (product.status !== "live" || amount.currency !== product.currency) {
      throw new InvestingError("product_not_open");
    }
    if (amount.minor < product.minimum.minor) {
      throw new InvestingError("below_minimum");
    }
    return product;
  }

  return {
    async listProducts() {
      await pause();
      return PRODUCTS.map(({ rateBps: _rate, taxBps: _tax, ...product }) => product);
    },

    async quote(productCode, amount): Promise<Quote> {
      await pause();
      const product = check(productCode, amount);
      const startsOn = kampalaDay(now());
      if (product.termDays === null) {
        return { productCode, amount, startsOn, maturesOn: null, interest: null, total: null };
      }
      const interest = netInterest(amount.minor, product, product.termDays);
      return {
        productCode,
        amount,
        startsOn,
        maturesOn: addDays(startsOn, product.termDays),
        interest: { currency: amount.currency, minor: interest },
        total: { currency: amount.currency, minor: amount.minor + interest },
      };
    },

    async requestInvestment(request: NewInvestment) {
      await pause();
      // Like the backend: a known key returns its investment, and the same key with
      // different details is refused rather than saving or replaying the wrong thing.
      const details = JSON.stringify([request.productCode, request.amount, request.atMaturity ?? null]);
      const known = byKey.get(request.idempotencyKey);
      if (known) {
        if (known.request !== details) {
          throw new InvestingError("key_reused");
        }
        return view(stored.get(known.id) as Stored);
      }
      if (demoClosing()) throw new InvestingError("account_closing");
      const product = check(request.productCode, request.amount);
      const unpaid = [...stored.values()].filter((entry) => view(entry).status === "awaiting_payment");
      if (unpaid.length >= MAX_UNPAID) {
        throw new InvestingError("too_many_unpaid");
      }
      nextId += 1;
      const entry: Stored = {
        id: `demo-investment-${nextId}`,
        product,
        amount: request.amount,
        atMaturity: product.termDays === null ? null : (request.atMaturity ?? "roll_over"),
        destinationId: null,
        payoutId: null,
        createdAtMs: now(),
        startDate: null,
        withdrawnAtMs: null,
      };
      stored.set(entry.id, entry);
      byKey.set(request.idempotencyKey, { id: entry.id, request: details });
      if (entry.amount.currency === "UGX") {
        addDemoPayable({
          id: entry.id,
          kind: "investment",
          title: product.name,
          amount: { currency: "UGX", minor: entry.amount.minor },
        });
      }
      return view(entry);
    },

    async listInvestments() {
      await pause();
      return [...stored.values()]
        .map(view)
        .filter((investment) => investment.status !== "cancelled")
        .reverse();
    },

    async getInvestment(investmentId) {
      await pause();
      const entry = stored.get(investmentId);
      if (!entry) {
        throw new InvestingError("not_found");
      }
      return view(entry);
    },

    async listPayoutAccounts() {
      await pause();
      return options.accounts === "none" ? [] : demoAccounts(now());
    },

    async withdrawEarly(request) {
      await pause();
      // Like the backend: a known key returns its answer, and the same key for another
      // investment or account is refused.
      const details = JSON.stringify([request.investmentId, request.destinationId]);
      const known = withdrawalKeys.get(request.idempotencyKey);
      if (known !== undefined && known !== details) {
        throw new InvestingError("key_reused");
      }
      const entry = stored.get(request.investmentId);
      if (!entry) {
        throw new InvestingError("not_found");
      }
      if (known !== undefined) {
        return view(entry);
      }
      if (!view(entry).canWithdraw) {
        throw new InvestingError("cannot_withdraw");
      }
      const account = demoAccounts(now()).find((candidate) => candidate.id === request.destinationId);
      if (!account || !account.ready) {
        throw new InvestingError("account_not_ready");
      }
      entry.withdrawnAtMs = now();
      withdrawalKeys.set(request.idempotencyKey, details);
      if (loseNextWithdrawal) {
        // Saved, but the answer never arrived: a retry with the same key gets it.
        loseNextWithdrawal = false;
        throw new InvestingError("unavailable");
      }
      return view(entry);
    },

    async chooseAtMaturity(choice) {
      await pause();
      const entry = stored.get(choice.investmentId);
      if (!entry) {
        throw new InvestingError("not_found");
      }
      if (!view(entry).canChangeMaturity) {
        throw new InvestingError("cannot_change_maturity");
      }
      if (choice.atMaturity === "pay_out") {
        // Like the backend: any approved account, even one still in its first day.
        const accounts = options.accounts === "none" ? [] : demoAccounts(now());
        if (!accounts.some((account) => account.id === choice.destinationId)) {
          throw new InvestingError("account_not_ready");
        }
      } else if (choice.destinationId !== null) {
        throw new InvestingError("invalid");
      }
      entry.atMaturity = choice.atMaturity;
      entry.destinationId = choice.destinationId;
      return view(entry);
    },

    async payOutMatured(request) {
      await pause();
      const entry = stored.get(request.investmentId);
      if (!entry) {
        throw new InvestingError("not_found");
      }
      const shown = view(entry);
      if (shown.payoutId !== null && entry.destinationId === request.destinationId) {
        // The same account asked twice is the same payout.
        return shown;
      }
      if (!shown.waitingForDestination) {
        throw new InvestingError("already_sent");
      }
      const account = demoAccounts(now()).find((candidate) => candidate.id === request.destinationId);
      if (!account || !account.ready) {
        throw new InvestingError("account_not_ready");
      }
      if (account.kind === "mobile_money" && shown.value.minor > MOBILE_MONEY_PER_PAYOUT) {
        throw new InvestingError("over_mobile_money_limit");
      }
      nextId += 1;
      entry.payoutId = `demo-payout-${nextId}`;
      entry.destinationId = request.destinationId;
      return view(entry);
    },
  };
}
