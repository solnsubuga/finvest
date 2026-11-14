/**
 * The Overview's figures: client money, the last seven days in and out, mobile money floats,
 * daily balancing, cash needed for maturities, where client money sits and what needs
 * attention (docs/overview.md, `GET /staff/overview`).
 *
 * Each section is null when the reader's role may not see it, and the screen leaves it out.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Permission } from "@/auth/types.ts";

export type Currency = "UGX" | "USD";

/** An amount in the smallest unit of its currency. Never a float. */
export interface Amount {
  minor: number;
  currency: Currency;
}

export interface InterestRun {
  runDate: string;
  investments: number;
  /** After tax: what clients earned. */
  net: Amount;
  finishedAt: string | null;
  /** Investments the run had not paid when it ended. */
  behind: number;
}

export interface DayFlow {
  day: string;
  moneyIn: Amount;
  moneyOut: Amount;
}

export interface MoneyFigures {
  clientMoneyHeld: Amount;
  inTransit: Amount;
  awaitingStart: Amount;
  clientsInvested: number;
  clientsVerified: number;
  interest: InterestRun | null;
  /** The last seven days, today last. */
  days: DayFlow[];
}

export interface Float {
  network: "mtn" | "airtel";
  balance: Amount;
  warningLine: Amount;
  low: boolean;
}

export interface Balancing {
  lastClosed: string | null;
  waitingForApproval: string | null;
  daysBehind: number;
}

export interface Week {
  firstDay: string;
  lastDay: string;
  investments: number;
  principal: Amount;
  /** Principal set to pay out at maturity: cash that leaves, before interest. */
  payOutPrincipal: Amount;
}

export interface Holding {
  productCode: string;
  productName: string;
  investments: number;
  clients: number;
  principal: Amount;
}

export type AttentionCode =
  | "float_low"
  | "payouts_waiting_for_float"
  | "review_queue"
  | "approvals_for_you"
  | "books_not_balanced"
  | "day_close_to_approve"
  | "interest_run_behind"
  | "interest_run_missing"
  | "maturities_not_closed";

export interface Attention {
  code: AttentionCode;
  tone: "warning" | "info";
  count: number;
  since: string | null;
  subject: string | null;
}

export interface Figures {
  today: string;
  currency: Currency;
  money: MoneyFigures | null;
  floats: Float[] | null;
  balancing: Balancing | null;
  maturities: Week[] | null;
  holdings: Holding[] | null;
  attention: Attention[];
}

export interface FiguresApi {
  load(currency: Currency): Promise<Figures>;
}

export class FiguresError extends Error {
  constructor(readonly code: "forbidden" | "not_connected" | "unavailable") {
    super(code);
    this.name = "FiguresError";
  }
}

export function figuresErrorMessage(error: unknown): string {
  if (error instanceof FiguresError && error.code === "not_connected") {
    return "The console is not connected to Finvest, so the figures cannot be shown.";
  }
  if (error instanceof FiguresError && error.code === "forbidden") return "Your role does not include the Overview's figures.";
  return "Finvest could not load the figures just now. Try again in a moment.";
}

interface AmountJson {
  minor: number;
  currency: Currency;
}

interface FiguresJson {
  today: string;
  currency: Currency;
  money: {
    client_money_held: AmountJson;
    in_transit: AmountJson;
    awaiting_start: AmountJson;
    clients_invested: number;
    clients_verified: number;
    interest: { run_date: string; investments: number; net: AmountJson; finished_at: string | null; behind: number } | null;
    days: { day: string; money_in: AmountJson; money_out: AmountJson }[];
  } | null;
  floats: { network: "mtn" | "airtel"; balance: AmountJson; warning_line: AmountJson; low: boolean }[] | null;
  balancing: { last_closed: string | null; waiting_for_approval: string | null; days_behind: number } | null;
  maturities: { first_day: string; last_day: string; investments: number; principal: AmountJson; pay_out_principal: AmountJson }[] | null;
  holdings: { product_code: string; product_name: string; investments: number; clients: number; principal: AmountJson }[] | null;
  attention: { code: AttentionCode; tone: "warning" | "info"; count: number; since: string | null; subject: string | null }[];
}

export function toFigures(body: FiguresJson): Figures {
  const money = body.money;
  return {
    today: body.today,
    currency: body.currency,
    money: money && {
      clientMoneyHeld: money.client_money_held,
      inTransit: money.in_transit,
      awaitingStart: money.awaiting_start,
      clientsInvested: money.clients_invested,
      clientsVerified: money.clients_verified,
      interest: money.interest && {
        runDate: money.interest.run_date,
        investments: money.interest.investments,
        net: money.interest.net,
        finishedAt: money.interest.finished_at,
        behind: money.interest.behind,
      },
      days: money.days.map((d) => ({ day: d.day, moneyIn: d.money_in, moneyOut: d.money_out })),
    },
    floats: body.floats && body.floats.map((f) => ({ network: f.network, balance: f.balance, warningLine: f.warning_line, low: f.low })),
    balancing: body.balancing && {
      lastClosed: body.balancing.last_closed,
      waitingForApproval: body.balancing.waiting_for_approval,
      daysBehind: body.balancing.days_behind,
    },
    maturities:
      body.maturities &&
      body.maturities.map((w) => ({
        firstDay: w.first_day,
        lastDay: w.last_day,
        investments: w.investments,
        principal: w.principal,
        payOutPrincipal: w.pay_out_principal,
      })),
    holdings:
      body.holdings &&
      body.holdings.map((h) => ({
        productCode: h.product_code,
        productName: h.product_name,
        investments: h.investments,
        clients: h.clients,
        principal: h.principal,
      })),
    attention: body.attention,
  };
}

export function createHttpFiguresApi(http: Http): FiguresApi {
  return {
    async load(currency) {
      try {
        return toFigures(await http.request<FiguresJson>("GET", `/staff/overview?currency=${currency}`));
      } catch (error) {
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) throw new FiguresError("forbidden");
        throw new FiguresError("unavailable");
      }
    },
  };
}

export const notConnectedFiguresApi: FiguresApi = {
  load: () => Promise.reject(new FiguresError("not_connected")),
};

const DAY = 86_400_000;

function isoDay(ms: number): string {
  return new Date(ms + 3 * 3_600_000).toISOString().slice(0, 10);
}

/** Sample figures for `pnpm dev` and previews, cut to what the role may see. Not real money. */
export function createStubFiguresApi(
  permissions: readonly Permission[],
  clock: () => number = Date.now,
  sample: "sample" | "empty" | "error" = "sample",
): FiguresApi {
  const has = (permission: Permission) => permissions.includes(permission);
  return {
    load(currency) {
      if (sample === "error") return Promise.reject(new FiguresError("unavailable"));
      const now = clock();
      const empty = sample === "empty";
      const ugx = currency === "UGX";
      // Dollars as about one 3,700th of the shillings, in whole dollars, by integer arithmetic.
      const amount = (shillings: number): Amount =>
        ugx ? { minor: shillings, currency } : { minor: ((shillings - (shillings % 3_700)) / 3_700) * 100, currency };
      const today = isoDay(now);
      const flows = [
        [38, 21],
        [22, 9],
        [96, 41],
        [71, 52],
        [84, 33],
        [67, 48],
        [58, 20],
      ];
      const money: MoneyFigures = {
        clientMoneyHeld: amount(empty ? 0 : 4_820_350_000),
        inTransit: amount(empty ? 0 : 31_500_000),
        awaitingStart: amount(empty ? 0 : 12_400_000),
        clientsInvested: empty ? 0 : 1_102,
        clientsVerified: empty ? 0 : 1_284,
        interest: empty ? null : { runDate: today, investments: 2_941, net: amount(1_310_400), finishedAt: new Date(now - 3 * 3_600_000).toISOString(), behind: 0 },
        days: flows.map(([moneyIn, moneyOut], n) => ({
          day: isoDay(now - (6 - n) * DAY),
          moneyIn: amount(empty ? 0 : (moneyIn ?? 0) * 1_000_000),
          moneyOut: amount(empty ? 0 : (moneyOut ?? 0) * 1_000_000),
        })),
      };
      const weeks: Week[] = [84, 141, 67, 201].map((principal, n) => {
        const first = n === 0 ? now : now + (7 * n - new Date(now).getUTCDay() + 1) * DAY;
        const last = now + (7 * (n + 1) - new Date(now).getUTCDay()) * DAY;
        return {
          firstDay: isoDay(first),
          lastDay: isoDay(last),
          investments: empty ? 0 : principal * 3,
          principal: amount(empty ? 0 : principal * 1_000_000),
          payOutPrincipal: amount(empty ? 0 : (principal - (principal % 2)) * 500_000),
        };
      });
      const attention: Attention[] = [];
      if (!empty && ugx && has("money_movement.view")) {
        attention.push({ code: "float_low", tone: "warning", count: 1, since: null, subject: "mtn" });
        attention.push({ code: "payouts_waiting_for_float", tone: "warning", count: 3, since: new Date(now - 50 * 60_000).toISOString(), subject: null });
      }
      if (!empty && has("review_queue.view")) {
        attention.push({ code: "review_queue", tone: "info", count: 7, since: new Date(now - 3 * 3_600_000).toISOString(), subject: null });
      }
      if (!empty) attention.push({ code: "approvals_for_you", tone: "info", count: 2, since: new Date(now - 55 * 60_000).toISOString(), subject: null });
      if (!empty && has("reconciliation.view")) {
        attention.push({ code: "day_close_to_approve", tone: "info", count: 1, since: null, subject: isoDay(now - DAY) });
      }
      return Promise.resolve({
        today,
        currency,
        money: has("money_movement.view") ? money : null,
        floats:
          has("money_movement.view") && ugx
            ? [
                { network: "mtn", balance: amount(empty ? 0 : 6_200_000), warningLine: amount(10_000_000), low: !empty },
                { network: "airtel", balance: amount(empty ? 0 : 23_750_000), warningLine: amount(10_000_000), low: false },
              ]
            : null,
        balancing: has("reconciliation.view")
          ? { lastClosed: empty ? null : isoDay(now - 2 * DAY), waitingForApproval: empty ? null : isoDay(now - DAY), daysBehind: empty ? 0 : 1 }
          : null,
        maturities: has("maturities.view") ? weeks : null,
        holdings: has("investments.view")
          ? empty
            ? []
            : [
                { productCode: "TREA-91", productName: "91 Day Treasury Bill", investments: 1_640, clients: 802, principal: amount(2_140_000_000) },
                { productCode: "TREA-182", productName: "182 Day Treasury Bill", investments: 980, clients: 511, principal: amount(1_760_000_000) },
                { productCode: "TREA-364", productName: "364 Day Treasury Bill", investments: 321, clients: 240, principal: amount(610_000_000) },
              ]
          : null,
        attention,
      });
    },
  };
}
