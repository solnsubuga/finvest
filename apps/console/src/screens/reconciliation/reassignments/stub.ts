import type { Amount, StatementLine } from "../api.ts";
import {
  ReassignmentError,
  type Party,
  type Proposal,
  type ReassignmentDetail,
  type ReassignmentsApi,
  type ReassignmentStatus,
  type ReassignmentSummary,
  type Refusal,
} from "./api.ts";

/**
 * Sample moves for `pnpm dev` and previews. Not real people or money, and not connected to
 * Finvest. Decisions change only this browser tab's copy. The rules here stand in for the
 * backend's: the screen itself decides nothing.
 */

export type StubCase = "sample" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const OTHER = "sample-colleague";

interface Payment {
  id: string;
  investmentId: string;
  client: Party;
  amount: Amount;
  method: "bank_transfer" | "mtn";
  line: StatementLine | null;
  /** Whether the money is still waiting with this client. */
  waiting: boolean;
}

interface Investment {
  id: string;
  client: Party;
  amount: Amount;
  waitingForPayment: boolean;
  hasPayment: boolean;
}

interface Stored extends ReassignmentSummary {
  proposedBy: string;
  fromClient: Party;
  toClient: Party;
  line: StatementLine | null;
}

const party = (clientId: string, name: string, accountNumber: string): Party => ({ clientId, name, accountNumber });

const NAMUKASA = party("-OdClient00000000011", "Sarah Namukasa", "2104 3378 15");
const OKELLO = party("-OdClient00000000012", "Peter Okello", "2104 5521 90");
const AKELLO = party("-OdClient00000000013", "Joan Akello", "2104 7710 42");
const SSEMPALA = party("-OdClient00000000014", "Brian Ssempala", "2104 1186 07");

function statementLine(id: string, valueDate: string, minor: number, reference: string, narrative: string): StatementLine {
  return { id, accountReference: "Stanbic trust 9030017", valueDate, amount: ugx(minor), reference, narrative };
}

function day(offset: number, now: Date): string {
  return new Date(now.getTime() + offset * 86_400_000).toISOString().slice(0, 10);
}

function ago(minutes: number, now: Date): string {
  return new Date(now.getTime() - minutes * 60_000).toISOString();
}

function samplePayments(now: Date): Payment[] {
  return [
    {
      id: "-OdBank000000000021A",
      investmentId: "-OdInv00000000000031",
      client: NAMUKASA,
      amount: ugx(4_000_000),
      method: "bank_transfer",
      line: statementLine("-OdLine00000000000041", day(-2, now), 4_000_000, "FV 2104552190", "RTGS IN PETER OKELLO"),
      waiting: true,
    },
    {
      id: "-OdBank000000000022A",
      investmentId: "-OdInv00000000000032",
      client: AKELLO,
      amount: ugx(1_500_000),
      method: "bank_transfer",
      line: statementLine("-OdLine00000000000042", day(-4, now), 1_500_000, "SCHOOL FEES", "EFT B SSEMPALA"),
      waiting: true,
    },
    {
      id: "-OdMomo000000000023A",
      investmentId: "-OdInv00000000000033",
      client: SSEMPALA,
      amount: ugx(500_000),
      method: "mtn",
      line: null,
      waiting: true,
    },
  ];
}

function sampleInvestments(): Investment[] {
  return [
    { id: "-OdInv00000000000051", client: OKELLO, amount: ugx(4_000_000), waitingForPayment: true, hasPayment: false },
    { id: "-OdInv00000000000052", client: SSEMPALA, amount: ugx(1_500_000), waitingForPayment: true, hasPayment: false },
    { id: "-OdInv00000000000053", client: OKELLO, amount: ugx(2_000_000), waitingForPayment: true, hasPayment: false },
  ];
}

function sample(now: Date, me: string, payments: Payment[]): Stored[] {
  const [okello, ssempala] = payments;
  if (!okello || !ssempala) return [];
  const base = (payment: Payment, to: Party, toInvestmentId: string, status: ReassignmentStatus, by: string, minutes: number) => ({
    depositId: payment.id,
    fromInvestmentId: payment.investmentId,
    toInvestmentId,
    amount: payment.amount,
    proposedAt: ago(minutes, now),
    proposedBy: by,
    fromClient: payment.client,
    toClient: to,
    line: payment.line,
    status,
    decidedAt: null,
    decisionNote: null,
    newDepositId: null,
  });
  return [
    {
      id: "-OdMove0000000000061",
      ...base(okello, OKELLO, "-OdInv00000000000051", "proposed", OTHER, 25),
      reason: "Matched to Sarah Namukasa's receipt by mistake. The reference is Peter Okello's account number and he sent his receipt the same day.",
    },
    {
      id: "-OdMove0000000000062",
      ...base(ssempala, SSEMPALA, "-OdInv00000000000052", "proposed", me, 70),
      reason: "The sender on the statement is Brian Ssempala, and Joan Akello's own transfer has not arrived.",
    },
    {
      id: "-OdMove0000000000063",
      ...base(okello, AKELLO, "-OdInv00000000000099", "sent_back", me, 60 * 26),
      decidedAt: ago(60 * 25, now),
      decisionNote: "The reference names Peter Okello, not Joan Akello. Check again before proposing.",
      reason: "Joan Akello says this is her transfer.",
    },
  ];
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function createStubReassignmentsApi(
  me: string,
  latency = 150,
  which: StubCase = "sample",
  now: () => Date = () => new Date(),
): ReassignmentsApi {
  const payments = samplePayments(now());
  const investments = sampleInvestments();
  let stored = which === "sample" ? sample(now(), me, payments) : [];
  const replies = new Map<string, ReassignmentDetail>();

  async function answer<T>(work: () => T): Promise<T> {
    await delay(latency);
    if (which === "error") throw new ReassignmentError("unavailable");
    return work();
  }

  function refusalFor(move: Stored): Refusal | null {
    const payment = payments.find((p) => p.id === move.depositId);
    const target = investments.find((i) => i.id === move.toInvestmentId);
    if (!payment) return "not_confirmed";
    return check(payment, target ?? null);
  }

  function check(payment: Payment, target: Investment | null): Refusal | null {
    if (payment.method !== "bank_transfer") return "mobile_money_refund_instead";
    if (!payment.line) return "not_matched_to_a_line";
    if (!payment.waiting) return "money_moved_on";
    if (!target) return "not_waiting_for_payment";
    if (target.client.clientId === payment.client.clientId) return "same_client";
    if (!target.waitingForPayment) return "not_waiting_for_payment";
    if (target.amount.minor !== payment.amount.minor) return "amount_differs_from_investment";
    if (target.hasPayment) return "already_has_a_payment";
    return null;
  }

  const names = (reference: string, who: Party) =>
    Boolean(who.accountNumber) && reference.replace(/\W/g, "").includes((who.accountNumber ?? "").replace(/\W/g, ""));

  function detail(move: Stored): ReassignmentDetail {
    const { proposedBy, ...rest } = move;
    const open = move.status === "proposed";
    return {
      ...rest,
      referenceNamesToClient: move.line ? names(move.line.reference, move.toClient) : false,
      referenceNamesFromClient: move.line ? names(move.line.reference, move.fromClient) : false,
      refusal: open ? refusalFor(move) : null,
      canDecide: open && proposedBy !== me,
      yours: proposedBy === me,
    };
  }

  function find(id: string): Stored {
    const move = stored.find((m) => m.id === id);
    if (!move) throw new ReassignmentError("not_found");
    return move;
  }

  function replace(next: Stored): ReassignmentDetail {
    stored = stored.map((m) => (m.id === next.id ? next : m));
    return detail(next);
  }

  function once(key: string, work: () => ReassignmentDetail): ReassignmentDetail {
    const earlier = replies.get(key);
    if (earlier) return earlier;
    const reply = work();
    replies.set(key, reply);
    return reply;
  }

  function open(id: string): Stored {
    const move = find(id);
    if (move.status !== "proposed") throw new ReassignmentError("conflict");
    return move;
  }

  return {
    list: () =>
      answer(() =>
        [...stored]
          .sort((a, b) => b.proposedAt.localeCompare(a.proposedAt))
          .map(
            (move): ReassignmentSummary => ({
              id: move.id,
              status: move.status,
              depositId: move.depositId,
              fromInvestmentId: move.fromInvestmentId,
              toInvestmentId: move.toInvestmentId,
              amount: move.amount,
              reason: move.reason,
              proposedAt: move.proposedAt,
              decidedAt: move.decidedAt,
              decisionNote: move.decisionNote,
              newDepositId: move.newDepositId,
            }),
          ),
      ),
    get: (id) => answer(() => detail(find(id))),
    propose: (key, proposal: Proposal) =>
      answer(() =>
        once(key, () => {
          const payment = payments.find((p) => p.id === proposal.depositId.trim());
          if (!payment) throw new ReassignmentError("deposit_not_found", "depositId");
          const target = investments.find((i) => i.id === proposal.toInvestmentId.trim());
          if (!target && payment.method === "bank_transfer" && payment.line) {
            throw new ReassignmentError("investment_not_found", "toInvestmentId");
          }
          const problem = check(payment, target ?? null);
          if (problem) {
            const aboutPayment = ["mobile_money_refund_instead", "not_matched_to_a_line", "money_moved_on"].includes(problem);
            throw new ReassignmentError(problem, aboutPayment ? "depositId" : "toInvestmentId");
          }
          if (stored.some((m) => m.status === "proposed" && (m.depositId === payment.id || m.toInvestmentId === target?.id))) {
            throw new ReassignmentError("conflict");
          }
          const move: Stored = {
            id: `-OdMoveStub${String(stored.length + 1).padStart(9, "0")}`,
            status: "proposed",
            depositId: payment.id,
            fromInvestmentId: payment.investmentId,
            toInvestmentId: target?.id ?? "",
            amount: payment.amount,
            reason: proposal.reason.trim(),
            proposedAt: now().toISOString(),
            proposedBy: me,
            decidedAt: null,
            decisionNote: null,
            newDepositId: null,
            fromClient: payment.client,
            toClient: target?.client ?? payment.client,
            line: payment.line,
          };
          stored = [move, ...stored];
          return detail(move);
        }),
      ),
    approve: (key, id) =>
      answer(() =>
        once(key, () => {
          const move = open(id);
          if (move.proposedBy === me) throw new ReassignmentError("forbidden");
          const problem = refusalFor(move);
          if (problem) throw new ReassignmentError(problem);
          const payment = payments.find((p) => p.id === move.depositId);
          const target = investments.find((i) => i.id === move.toInvestmentId);
          if (payment) payment.waiting = false;
          if (target) {
            target.hasPayment = true;
            target.waitingForPayment = false;
          }
          return replace({ ...move, status: "applied", decidedAt: now().toISOString(), newDepositId: "-OdBankMoved0000001A" });
        }),
      ),
    sendBack: (key, id, note) =>
      answer(() =>
        once(key, () => {
          const move = open(id);
          if (move.proposedBy === me) throw new ReassignmentError("forbidden");
          return replace({ ...move, status: "sent_back", decidedAt: now().toISOString(), decisionNote: note.trim() });
        }),
      ),
    withdraw: (key, id) =>
      answer(() =>
        once(key, () => {
          const move = open(id);
          if (move.proposedBy !== me) throw new ReassignmentError("forbidden");
          return replace({ ...move, status: "withdrawn", decidedAt: now().toISOString() });
        }),
      ),
  };
}
