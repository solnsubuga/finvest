import type { Http } from "@/api/http.ts";
import {
  toTaxError,
  type Amount,
  type Currency,
  type MonthReport,
  type NewRemittance,
  type Remittance,
  type TaxApi,
  type TaxMonth,
  type TaxScreen,
} from "./api.ts";

interface MonthJson {
  month: string;
  withheld: Amount;
  remitted: Amount;
  waiting: Amount;
  outstanding: Amount;
  due_on: string;
  overdue: boolean;
}

interface RemittanceJson {
  id: string;
  month: string;
  amount: Amount;
  ura_reference: string;
  bank_reference: string;
  paid_on: string;
  note: string;
  status: Remittance["status"];
  proposed_by: string;
  proposed_by_name: string | null;
  proposed_at: string;
  decided_by: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  allowed: Remittance["allowed"];
}

interface ScreenJson {
  currency: Currency;
  held_for_ura: Amount;
  months: MonthJson[];
  remittances: RemittanceJson[];
}

interface ReportJson {
  month: string;
  currency: Currency;
  gross: Amount;
  withheld: Amount;
  summary: MonthJson;
  payees: {
    client_id: string;
    name: string | null;
    account_number: string | null;
    tin: string | null;
    gross: Amount;
    tax: Amount;
  }[];
  tins_shown: boolean;
}

function toMonth(json: MonthJson): TaxMonth {
  return {
    month: json.month,
    withheld: json.withheld,
    remitted: json.remitted,
    waiting: json.waiting,
    outstanding: json.outstanding,
    dueOn: json.due_on,
    overdue: json.overdue,
  };
}

export function toScreen(json: ScreenJson): TaxScreen {
  return {
    currency: json.currency,
    heldForUra: json.held_for_ura,
    months: json.months.map(toMonth),
    remittances: json.remittances.map((r) => ({
      id: r.id,
      month: r.month,
      amount: r.amount,
      uraReference: r.ura_reference,
      bankReference: r.bank_reference,
      paidOn: r.paid_on,
      note: r.note,
      status: r.status,
      proposedBy: r.proposed_by,
      proposedByName: r.proposed_by_name,
      proposedAt: r.proposed_at,
      decidedBy: r.decided_by,
      decidedByName: r.decided_by_name,
      decidedAt: r.decided_at,
      decisionReason: r.decision_reason,
      allowed: r.allowed,
    })),
  };
}

function toReport(json: ReportJson): MonthReport {
  return {
    month: json.month,
    currency: json.currency,
    gross: json.gross,
    withheld: json.withheld,
    summary: toMonth(json.summary),
    payees: json.payees.map((p) => ({
      clientId: p.client_id,
      name: p.name,
      accountNumber: p.account_number,
      tin: p.tin,
      gross: p.gross,
      tax: p.tax,
    })),
    tinsShown: json.tins_shown,
  };
}

export function createHttpTaxApi(http: Http): TaxApi {
  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toTaxError(error);
    }
  }
  const query = (currency: Currency) => `currency=${encodeURIComponent(currency)}`;
  const month = (value: string) => encodeURIComponent(value);
  return {
    getScreen: (currency) => call(async () => toScreen(await http.request<ScreenJson>("GET", `/staff/tax?${query(currency)}`))),
    getMonth: (currency, value) =>
      call(async () => toReport(await http.request<ReportJson>("GET", `/staff/tax/months/${month(value)}?${query(currency)}`))),
    exportMonth: (currency, value) =>
      call(async () => ({
        file: await http.download(`/staff/tax/months/${month(value)}/payees.csv?${query(currency)}`),
        filename: `finvest-withholding-${currency.toLowerCase()}-${value}.csv`,
      })),
    record: (key, payment: NewRemittance) =>
      call(async () =>
        toScreen(
          await http.request<ScreenJson>(
            "POST",
            "/staff/tax/remittances",
            {
              currency: payment.currency,
              month: payment.month,
              amount_minor: payment.amountMinor,
              ura_reference: payment.uraReference,
              bank_reference: payment.bankReference,
              paid_on: payment.paidOn,
              note: payment.note,
            },
            { idempotencyKey: key },
          ),
        ),
      ),
    approve: (key, id) =>
      call(async () =>
        toScreen(
          await http.request<ScreenJson>("POST", `/staff/tax/remittances/${encodeURIComponent(id)}/approve`, undefined, {
            idempotencyKey: key,
          }),
        ),
      ),
    decline: (key, id, reason) =>
      call(async () =>
        toScreen(
          await http.request<ScreenJson>(
            "POST",
            `/staff/tax/remittances/${encodeURIComponent(id)}/decline`,
            { reason },
            { idempotencyKey: key },
          ),
        ),
      ),
  };
}
