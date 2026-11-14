/**
 * Sample withholding figures for `pnpm dev` and preview builds. Not Finvest's
 * books. It keeps the backend's rules that a screen shows: only an ended month
 * can be paid, never more than it owes, and the person who recorded a payment
 * never approves it.
 */

import {
  TaxError,
  type Amount,
  type Currency,
  type MonthReport,
  type NewRemittance,
  type Payee,
  type Remittance,
  type TaxApi,
  type TaxMonth,
  type TaxScreen,
} from "./api.ts";

interface Reader {
  id: string;
  name: string;
}

export interface StubOptions {
  /** No tax withheld yet and no payments: a new Finvest. */
  empty?: boolean;
  /** The Kampala day the sample treats as today. */
  today?: string;
}

const OTHER = "stub-grace";

const PAYEES: readonly Omit<Payee, "gross" | "tax">[] = [
  { clientId: "-NstubClient00000001", name: "NAKATO Amara", accountNumber: "1001004827", tin: "1000123456" },
  { clientId: "-NstubClient00000002", name: "OKELLO Peter", accountNumber: "1002338410", tin: null },
  { clientId: "-NstubClient00000003", name: "NAMUBIRU Sarah", accountNumber: "1004417726", tin: "1009988776" },
  { clientId: "-NstubClient00000004", name: "MUGISHA Daniel", accountNumber: "1007720153", tin: "1004455661" },
  { clientId: "-NstubClient00000005", name: "ATIM Grace", accountNumber: "1003301948", tin: null },
];
// Interest before tax each client earned in a month, in shillings; tax is 15% of it, rounded down.
const EARNED = [31_164, 62_329, 8_219, 140_250, 17_808];

function monthsBack(today: string, count: number): string {
  const [year, month] = today.split("-").map(Number) as [number, number];
  const index = year * 12 + (month - 1) - count;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

function dueOn(month: string): string {
  return `${monthsBack(`${month}-01`, -1)}-15`;
}

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });

export function createStubTaxApi(reader: Reader, options: StubOptions = {}): TaxApi {
  const today = options.today ?? new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());
  const thisMonth = today.slice(0, 7);
  const months = Array.from({ length: 12 }, (_, index) => monthsBack(today, index));
  // Clients joined over time: the oldest months have fewer of them.
  const payeesIn = (month: string): Payee[] => {
    if (options.empty) return [];
    const age = months.indexOf(month);
    const count = age < 0 ? 0 : Math.max(0, Math.min(PAYEES.length, 9 - age));
    const scale = month === thisMonth ? Number(today.slice(8, 10)) / 30 : 1;
    return PAYEES.slice(0, count).map((payee, index) => {
      const gross = Math.floor((EARNED[index] ?? 0) * scale);
      return { ...payee, gross: ugx(gross), tax: ugx(Math.floor((gross * 15) / 100)) };
    });
  };
  const withheld = (month: string) => payeesIn(month).reduce((sum, payee) => sum + payee.tax.minor, 0);

  let sequence = 0;
  const remittances: Remittance[] = [];
  if (!options.empty) {
    // Every ended month but the last is paid; the last waits for a second person.
    for (const month of months.slice(2).reverse()) {
      if (withheld(month) === 0) continue;
      remittances.unshift(made(month, withheld(month), "approved", OTHER, reader.id));
    }
    remittances.unshift(made(months[1] ?? thisMonth, withheld(months[1] ?? thisMonth), "proposed", OTHER, null));
  }

  function made(month: string, minor: number, status: Remittance["status"], by: string, decidedBy: string | null): Remittance {
    sequence += 1;
    const paidOn = `${monthsBack(`${month}-01`, -1)}-${status === "proposed" ? today.slice(8, 10) : "12"}`;
    return {
      id: `-NstubTaxPay${String(sequence).padStart(8, "0")}`,
      month,
      amount: ugx(minor),
      uraReference: `PRN22${String(400_000 + sequence * 7919).padStart(8, "0")}`,
      bankReference: `FT26${String(310 + sequence)}TRX${sequence}`,
      paidOn: paidOn > today ? today : paidOn,
      note: `Withholding tax on interest, ${month}`,
      status,
      proposedBy: by,
      proposedByName: by === OTHER ? "Grace Atim" : reader.name,
      proposedAt: `${paidOn > today ? today : paidOn}T09:12:00Z`,
      decidedBy,
      decidedByName: decidedBy ? reader.name : null,
      decidedAt: decidedBy ? `${paidOn > today ? today : paidOn}T11:40:00Z` : null,
      decisionReason: null,
      allowed: [],
    };
  }

  const sum = (month: string, status: Remittance["status"]) =>
    remittances.filter((r) => r.month === month && r.status === status).reduce((total, r) => total + r.amount.minor, 0);

  function summary(month: string): TaxMonth {
    const owed = withheld(month) - sum(month, "approved") - sum(month, "proposed");
    return {
      month,
      withheld: ugx(withheld(month)),
      remitted: ugx(sum(month, "approved")),
      waiting: ugx(sum(month, "proposed")),
      outstanding: ugx(owed),
      dueOn: dueOn(month),
      overdue: owed > 0 && today > dueOn(month),
    };
  }

  function screen(currency: Currency): TaxScreen {
    if (currency === "USD") return { currency, heldForUra: { minor: 0, currency }, months: months.map((m) => ({ ...summary(m), withheld: { minor: 0, currency }, remitted: { minor: 0, currency }, waiting: { minor: 0, currency }, outstanding: { minor: 0, currency }, overdue: false })), remittances: [] };
    const held = months.reduce((total, m) => total + withheld(m) - sum(m, "approved"), 0);
    return {
      currency,
      heldForUra: ugx(held),
      months: months.map(summary),
      remittances: remittances.map((r) => ({
        ...r,
        allowed: r.status === "proposed" && r.proposedBy !== reader.id ? (["approve", "decline"] as const) : [],
      })),
    };
  }

  function find(id: string): Remittance {
    const found = remittances.find((r) => r.id === id);
    if (!found) throw new TaxError("not_found");
    if (found.status !== "proposed") throw new TaxError("conflict");
    if (found.proposedBy === reader.id) throw new TaxError("forbidden");
    return found;
  }

  return {
    getScreen: async (currency) => screen(currency),
    getMonth: async (currency, month): Promise<MonthReport> => {
      const payees = currency === "UGX" ? payeesIn(month) : [];
      return {
        month,
        currency,
        gross: { minor: payees.reduce((t, p) => t + p.gross.minor, 0), currency },
        withheld: { minor: payees.reduce((t, p) => t + p.tax.minor, 0), currency },
        summary: currency === "UGX" ? summary(month) : screen(currency).months.find((m) => m.month === month) ?? summary(month),
        payees,
        tinsShown: true,
      };
    },
    exportMonth: async (currency, month) => {
      const rows = (currency === "UGX" ? payeesIn(month) : []).map(
        (p) => `${month},${p.name ?? ""},${p.tin ?? ""},${p.accountNumber ?? ""},${currency},${p.gross.minor},${p.tax.minor}`,
      );
      const text = ["Month,Payee name,Payee TIN,Account number,Currency,Interest before tax,Tax withheld", ...rows, ""].join("\r\n");
      return { file: new Blob([text], { type: "text/csv" }), filename: `finvest-withholding-${currency.toLowerCase()}-${month}.csv` };
    },
    record: async (_key, payment: NewRemittance) => {
      if (payment.month >= thisMonth) throw new TaxError("month_not_ended");
      if (payment.paidOn > today) throw new TaxError("paid_in_the_future");
      if (payment.paidOn < `${payment.month}-01`) throw new TaxError("paid_before_the_month");
      if (payment.currency !== "UGX" || payment.amountMinor > summary(payment.month).outstanding.minor) {
        throw new TaxError("more_than_outstanding");
      }
      if (remittances.some((r) => r.status !== "declined" && r.uraReference === payment.uraReference.trim())) {
        throw new TaxError("already_recorded");
      }
      sequence += 1;
      remittances.unshift({
        id: `-NstubTaxPay${String(sequence).padStart(8, "0")}`,
        month: payment.month,
        amount: ugx(payment.amountMinor),
        uraReference: payment.uraReference.trim(),
        bankReference: payment.bankReference.trim(),
        paidOn: payment.paidOn,
        note: payment.note.trim(),
        status: "proposed",
        proposedBy: reader.id,
        proposedByName: reader.name,
        proposedAt: new Date().toISOString(),
        decidedBy: null,
        decidedByName: null,
        decidedAt: null,
        decisionReason: null,
        allowed: [],
      });
      return screen(payment.currency);
    },
    approve: async (_key, id) => {
      const found = find(id);
      Object.assign(found, { status: "approved", decidedBy: reader.id, decidedByName: reader.name, decidedAt: new Date().toISOString() });
      return screen(found.amount.currency);
    },
    decline: async (_key, id, reason) => {
      if (!reason.trim()) throw new TaxError("reason_required");
      const found = find(id);
      Object.assign(found, {
        status: "declined",
        decidedBy: reader.id,
        decidedByName: reader.name,
        decidedAt: new Date().toISOString(),
        decisionReason: reason.trim(),
      });
      return screen(found.amount.currency);
    },
  };
}
