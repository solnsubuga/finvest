import type { Amount } from "../api.ts";
import {
  CorrectionError,
  kindOf,
  type CorrectionDetail,
  type CorrectionSummary,
  type Kind,
  type Method,
  type MistakenPaymentsApi,
  type Proposal,
  type Refusal,
} from "./api.ts";

/**
 * Sample mistaken payments for `pnpm dev` and previews. Not real people or money, and not
 * connected to Finvest. Decisions change only this browser tab's copy. The rules here stand in
 * for the backend's: the screen itself decides nothing.
 */

export type StubCase = "sample" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const OTHER = "sample-colleague";

/** Where a sample payment's money is now. */
type Where = "waiting" | "running" | "left" | "reversed";

interface Payment {
  id: string;
  clientId: string;
  investmentId: string;
  amount: Amount;
  method: Method;
  where: Where;
  /** What the client owes for it, in minor units. */
  owed: number;
  opened: boolean;
  cleared: boolean;
}

interface Stored extends CorrectionSummary {
  proposedBy: string;
}

function samplePayments(): Payment[] {
  const payment = (id: string, client: string, minor: number, method: Method, where: Where): Payment => ({
    id,
    clientId: `-OdClient0000000${client}`,
    investmentId: `-OdInvest0000000${client}`,
    amount: ugx(minor),
    method,
    where,
    owed: 0,
    opened: false,
    cleared: false,
  });
  return [
    payment("-OdDeposit000000031", "0021", 2_500_000, "mtn", "waiting"),
    payment("-OdDeposit000000032", "0022", 800_000, "bank_transfer", "waiting"),
    payment("-OdDeposit000000027", "0023", 1_200_000, "bank_transfer", "left"),
    payment("-OdDeposit000000028", "0024", 450_000, "airtel", "left"),
    payment("-OdDeposit000000033", "0025", 3_000_000, "bank_transfer", "running"),
  ];
}

function sampleSteps(me: string, now: number, payments: Payment[]): Stored[] {
  const ago = (minutes: number) => new Date(now - minutes * 60 * 1000).toISOString();
  const find = (id: string) => payments.find((payment) => payment.id === id) as Payment;
  const stored = (id: string, step: Stored["step"], depositId: string, by: string, minutes: number, reason: string): Stored => {
    const payment = find(depositId);
    return {
      id,
      step,
      status: "proposed",
      depositId,
      clientId: payment.clientId,
      investmentId: step === "reverse" ? payment.investmentId : null,
      amount: payment.amount,
      reason,
      proposedAt: ago(minutes),
      decidedAt: null,
      decisionNote: null,
      failureCode: null,
      proposedBy: by,
    };
  };
  // One receivable already open and recorded, so recovering or writing off can be tried.
  const owedOne = find("-OdDeposit000000028");
  owedOne.opened = true;
  owedOne.owed = owedOne.amount.minor;
  return [
    stored("-OdReversal00000001", "reverse", "-OdDeposit000000031", OTHER, 40, "MTN confirmed this payment by mistake. Their statement shows no money reached our wallet."),
    stored("-OdOwed000000000001", "open", "-OdDeposit000000027", OTHER, 65, "The bank recalled this transfer after the investment matured and was paid out."),
    stored("-OdReversal00000002", "reverse", "-OdDeposit000000032", me, 20, "The bank reversed this transfer the next day. See the statement for 12 November."),
    {
      ...stored("-OdOwed000000000002", "open", "-OdDeposit000000028", OTHER, 2900, "Airtel reversed the collection after the refund was paid."),
      status: "applied" as const,
      decidedAt: ago(2800),
    },
  ].sort((a, b) => b.proposedAt.localeCompare(a.proposedAt));
}

function summaryOf(stored: Stored): CorrectionSummary {
  const summary: Partial<Stored> = structuredClone(stored);
  delete summary.proposedBy;
  return summary as CorrectionSummary;
}

export function createStubMistakenPaymentsApi(
  me: string,
  delay = 150,
  which: StubCase = "sample",
  clock: () => Date = () => new Date(),
): MistakenPaymentsApi {
  const payments = which === "sample" ? samplePayments() : [];
  const steps: Stored[] = which === "sample" ? sampleSteps(me, clock().getTime(), payments) : [];
  let counter = 0;
  const answers = new Map<string, CorrectionDetail>();

  const wait = async () => {
    if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
    if (which === "error") throw new CorrectionError("unavailable");
  };
  const paymentOf = (depositId: string) => payments.find((payment) => payment.id === depositId);

  function refusal(step: Proposal["step"], payment: Payment): Refusal | null {
    if (payment.where === "reversed") return "already_reversed";
    if (step === "reverse") return payment.where === "waiting" ? null : "money_moved_on";
    if (step === "open") {
      if (payment.where === "running") return "unwind_first";
      if (payment.where === "waiting") return "reverse_instead";
      return payment.cleared ? "already_cleared" : payment.opened ? "already_open" : null;
    }
    if (!payment.opened) return "not_open";
    return payment.cleared ? "already_cleared" : null;
  }

  function detail(stored: Stored): CorrectionDetail {
    const payment = paymentOf(stored.depositId) as Payment;
    const open = stored.status === "proposed";
    const { proposedBy, ...summary } = stored;
    return {
      ...summary,
      method: payment.method,
      balance: ugx(stored.step === "reverse" ? (payment.where === "waiting" ? payment.amount.minor : 0) : payment.owed),
      refusal: open ? refusal(stored.step, payment) : null,
      canDecide: open && proposedBy !== me,
      yours: proposedBy === me,
    };
  }

  function find(kind: Kind, id: string): Stored {
    const stored = steps.find((step) => step.id === id && kindOf(step.step) === kind);
    if (!stored) throw new CorrectionError("not_found");
    return stored;
  }

  function replay(key: string, work: () => CorrectionDetail): CorrectionDetail {
    const known = answers.get(key);
    if (known) return structuredClone(known);
    const answer = work();
    answers.set(key, answer);
    return structuredClone(answer);
  }

  function open(stored: Stored) {
    if (stored.status !== "proposed") throw new CorrectionError("conflict");
  }

  return {
    async list() {
      await wait();
      return steps.map(summaryOf);
    },
    async outstanding() {
      await wait();
      return steps
        .filter((stored) => stored.step === "open" && stored.status === "applied" && !(paymentOf(stored.depositId) as Payment).cleared)
        .map(summaryOf);
    },
    async get(kind, id) {
      await wait();
      return structuredClone(detail(find(kind, id)));
    },
    async propose(key, proposal) {
      await wait();
      return replay(key, () => {
        const payment = paymentOf(proposal.depositId);
        if (!payment) throw new CorrectionError("deposit_not_found", "depositId");
        const kind = kindOf(proposal.step);
        if (steps.some((stored) => stored.depositId === payment.id && kindOf(stored.step) === kind && stored.status === "proposed")) {
          throw new CorrectionError("conflict");
        }
        const problem = refusal(proposal.step, payment);
        if (problem) throw new CorrectionError(problem, "depositId");
        counter += 1;
        const stored: Stored = {
          id: `-OdSample${String(counter).padStart(10, "0")}`,
          step: proposal.step,
          status: "proposed",
          depositId: payment.id,
          clientId: payment.clientId,
          investmentId: kind === "reversal" ? payment.investmentId : null,
          amount: payment.amount,
          reason: proposal.reason,
          proposedAt: clock().toISOString(),
          decidedAt: null,
          decisionNote: null,
          failureCode: null,
          proposedBy: me,
        };
        steps.unshift(stored);
        return detail(stored);
      });
    },
    async approve(key, kind, id) {
      await wait();
      return replay(key, () => {
        const stored = find(kind, id);
        open(stored);
        if (stored.proposedBy === me) throw new CorrectionError("forbidden");
        const payment = paymentOf(stored.depositId) as Payment;
        const problem = refusal(stored.step, payment);
        if (problem) throw new CorrectionError(problem, "depositId");
        stored.decidedAt = clock().toISOString();
        if (stored.step === "reverse") {
          // The worker carries it out at once in the sample.
          stored.status = "done";
          payment.where = "reversed";
        } else {
          stored.status = "applied";
          if (stored.step === "open") {
            payment.opened = true;
            payment.owed = payment.amount.minor;
          } else {
            payment.cleared = true;
            payment.owed = 0;
          }
        }
        return detail(stored);
      });
    },
    async sendBack(key, kind, id, note) {
      await wait();
      return replay(key, () => {
        const stored = find(kind, id);
        open(stored);
        if (stored.proposedBy === me) throw new CorrectionError("forbidden");
        stored.status = "sent_back";
        stored.decidedAt = clock().toISOString();
        stored.decisionNote = note;
        return detail(stored);
      });
    },
    async withdraw(key, kind, id) {
      await wait();
      return replay(key, () => {
        const stored = find(kind, id);
        open(stored);
        if (stored.proposedBy !== me) throw new CorrectionError("forbidden");
        stored.status = "withdrawn";
        stored.decidedAt = clock().toISOString();
        return detail(stored);
      });
    },
  };
}
