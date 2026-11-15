import type { Amount } from "../api.ts";
import { RefundError, type CardRefundsApi, type Proposal, type Refusal, type RefundDetail, type RefundSummary } from "./api.ts";

/**
 * Sample card refunds for `pnpm dev` and previews. Not real people, cards or money, and not
 * connected to Finvest or Pesapal. Decisions change only this browser tab's copy. The rules here
 * stand in for the backend's: the screen itself decides nothing. In the sample the worker asks
 * Pesapal at once, and Pesapal takes the request.
 */

export type StubCase = "sample" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const OTHER = "sample-colleague";

/** Where a sample card payment's money is now. */
type Where = "waiting" | "running" | "refunded";

interface Payment {
  id: string;
  clientId: string;
  investmentId: string;
  amount: number;
  charge: number;
  brand: string;
  lastFour: string;
  where: Where;
}

interface Stored extends RefundSummary {
  proposedBy: string;
}

function samplePayments(): Payment[] {
  const payment = (id: string, client: string, amount: number, brand: string, lastFour: string, where: Where): Payment => ({
    id,
    clientId: `-OdClient0000000${client}`,
    investmentId: `-OdInvest0000000${client}`,
    amount,
    // 3.5% on top, rounded up to the shilling, as the sample Settings say.
    charge: Math.ceil((amount * 350) / 10_000),
    brand,
    lastFour,
    where,
  });
  return [
    payment("-OdCardPay00000041", "0031", 1_000_000, "Visa", "0010", "waiting"),
    payment("-OdCardPay00000042", "0032", 2_500_000, "Mastercard", "4417", "waiting"),
    payment("-OdCardPay00000043", "0033", 600_000, "Visa", "8812", "waiting"),
    payment("-OdCardPay00000044", "0034", 1_500_000, "Visa", "2290", "running"),
  ];
}

export function createStubCardRefundsApi(
  me: string,
  delay = 150,
  which: StubCase = "sample",
  clock: () => Date = () => new Date(),
): CardRefundsApi {
  const payments = which === "sample" ? samplePayments() : [];
  const refunds: Stored[] = [];
  const answers = new Map<string, RefundDetail>();
  let counter = 0;

  const wait = async () => {
    if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
    if (which === "error") throw new RefundError("unavailable");
  };
  const paymentOf = (id: string) => payments.find((payment) => payment.id === id);
  const now = () => clock().toISOString();

  function refusal(payment: Payment): Refusal | null {
    if (payment.where === "refunded") return "already_taken_back";
    return payment.where === "waiting" ? null : "money_moved_on";
  }

  function make(payment: Payment, by: string, minutes: number, reason: string, finvestErred: boolean): Stored {
    counter += 1;
    const charge = payment.charge;
    return {
      id: `-OdRefund${String(counter).padStart(10, "0")}`,
      status: "proposed",
      depositId: payment.id,
      clientId: payment.clientId,
      investmentId: payment.investmentId,
      amount: ugx(payment.amount),
      charge: ugx(charge),
      finvestErred,
      total: ugx(payment.amount + (finvestErred ? charge : 0)),
      reason,
      proposedAt: new Date(clock().getTime() - minutes * 60_000).toISOString(),
      decidedAt: null,
      decisionNote: null,
      sentAt: null,
      found: null,
      foundAt: null,
      foundNote: null,
      doneAt: null,
      failureCode: null,
      proposedBy: by,
    };
  }

  if (which === "sample") {
    const [first, second, third] = payments as [Payment, Payment, Payment];
    refunds.push(make(first, OTHER, 10, "The client asked to cancel the 91 day bill before it started.", false));
    refunds.push(make(second, me, 25, "We showed the wrong rate on the offer, so the client cancelled.", true));
    // One already asked of Pesapal, waiting for Pesapal to pay it.
    const asked = make(third, OTHER, 1500, "The client paid twice for the same goal saving by mistake.", false);
    Object.assign(asked, { status: "requested", decidedAt: new Date(clock().getTime() - 1400 * 60_000).toISOString(), sentAt: new Date(clock().getTime() - 1399 * 60_000).toISOString() });
    third.where = "refunded";
    refunds.push(asked);
    refunds.sort((a, b) => b.proposedAt.localeCompare(a.proposedAt));
  }

  function detail(stored: Stored): RefundDetail {
    const payment = paymentOf(stored.depositId) as Payment;
    const open = stored.status === "proposed";
    const { proposedBy, ...summary } = stored;
    const mine = proposedBy === me;
    const waitingOnPesapal =
      (stored.status === "requested" && (stored.found === null || stored.found === "requested")) ||
      (stored.status === "uncertain" && stored.found === null);
    return {
      ...summary,
      cardBrand: payment.brand,
      cardLastFour: payment.lastFour,
      waiting: ugx(payment.where === "waiting" ? payment.amount : 0),
      refusal: open ? refusal(payment) : null,
      canDecide: open && !mine,
      canRecordFinding: waitingOnPesapal && !mine,
      yours: mine,
    };
  }

  function find(id: string): Stored {
    const stored = refunds.find((refund) => refund.id === id);
    if (!stored) throw new RefundError("not_found");
    return stored;
  }

  function replay(key: string, work: () => RefundDetail): RefundDetail {
    const known = answers.get(key);
    if (known) return structuredClone(known);
    const answer = work();
    answers.set(key, answer);
    return structuredClone(answer);
  }

  function proposed(stored: Stored) {
    if (stored.status !== "proposed") throw new RefundError("conflict");
  }

  const summaryOf = (stored: Stored): RefundSummary => {
    const summary: Partial<Stored> = structuredClone(stored);
    delete summary.proposedBy;
    return summary as RefundSummary;
  };

  return {
    async list() {
      await wait();
      return refunds.map(summaryOf);
    },
    async get(id) {
      await wait();
      return structuredClone(detail(find(id)));
    },
    async propose(key, proposal: Proposal) {
      await wait();
      return replay(key, () => {
        const payment = paymentOf(proposal.depositId);
        if (!payment) throw new RefundError("deposit_not_found", "depositId");
        const live = refunds.some((refund) => refund.depositId === payment.id && !["sent_back", "withdrawn", "failed"].includes(refund.status));
        if (live) throw new RefundError("conflict");
        const problem = refusal(payment);
        if (problem) throw new RefundError(problem, "depositId");
        const stored = make(payment, me, 0, proposal.reason, proposal.finvestErred);
        stored.proposedAt = now();
        refunds.unshift(stored);
        return detail(stored);
      });
    },
    async approve(key, id) {
      await wait();
      return replay(key, () => {
        const stored = find(id);
        proposed(stored);
        if (stored.proposedBy === me) throw new RefundError("forbidden");
        const payment = paymentOf(stored.depositId) as Payment;
        const problem = refusal(payment);
        if (problem) throw new RefundError(problem, "depositId");
        // The worker holds the money and Pesapal takes the request at once in the sample.
        stored.status = "requested";
        stored.decidedAt = now();
        stored.sentAt = now();
        payment.where = "refunded";
        return detail(stored);
      });
    },
    async sendBack(key, id, note) {
      await wait();
      return replay(key, () => {
        const stored = find(id);
        proposed(stored);
        if (stored.proposedBy === me) throw new RefundError("forbidden");
        Object.assign(stored, { status: "sent_back", decidedAt: now(), decisionNote: note });
        return detail(stored);
      });
    },
    async withdraw(key, id) {
      await wait();
      return replay(key, () => {
        const stored = find(id);
        proposed(stored);
        if (stored.proposedBy !== me) throw new RefundError("forbidden");
        Object.assign(stored, { status: "withdrawn", decidedAt: now() });
        return detail(stored);
      });
    },
    async recordFinding(key, id, found, note) {
      await wait();
      return replay(key, () => {
        const stored = find(id);
        if (stored.proposedBy === me) throw new RefundError("forbidden");
        if (!detail(stored).canRecordFinding) throw new RefundError("conflict");
        Object.assign(stored, { found, foundAt: now(), foundNote: note });
        // The worker carries out what was found at once in the sample.
        const payment = paymentOf(stored.depositId) as Payment;
        if (found === "paid") Object.assign(stored, { status: "done", doneAt: now() });
        else if (found === "not_paid") {
          Object.assign(stored, { status: "failed", failureCode: "not_paid_by_provider" });
          payment.where = "waiting";
        } else stored.status = "requested";
        return detail(stored);
      });
    },
  };
}
