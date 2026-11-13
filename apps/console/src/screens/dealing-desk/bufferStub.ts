import type { Amount, Currency } from "./api.ts";
import {
  BufferError,
  type BufferApi,
  type BufferScreen,
  type BufferStep,
  type Movement,
  type NewMovement,
  type NewRules,
  type Rules,
  type RulesProposal,
} from "./buffer.ts";

/**
 * A sample buffer for `pnpm dev` and previews. Not real money and not connected to Finvest.
 * Commands change only this browser tab's copy. The arithmetic and the two-person rule here
 * stand in for the backend. The screen itself does none.
 */

const M = 1_000_000;
const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });

export const BUFFER_COLLEAGUE = { id: "-Nstaffdaniel0000000", name: "Daniel Okello" };

type Stored<T> = Omit<T, "allowed">;

const SEED: Rules = {
  effectiveFrom: "2026-01-01",
  target: ugx(150 * M),
  floor: ugx(100 * M),
  fromBufferUpTo: ugx(5 * M),
  payWithinWorkingDays: 2,
};

export function createStubBufferApi(
  me: { id: string },
  sample: "sample" | "empty" = "sample",
  now: () => Date = () => new Date(),
): BufferApi {
  const versions: Rules[] = [SEED];
  const proposals: Stored<RulesProposal>[] = [];
  const movements: Stored<Movement>[] = [];
  let clientMoneyShort = 0;
  if (sample === "sample") {
    // The buffer after promised withdrawals is UGX 118.5m, as on the desk's sample (stub.ts).
    clientMoneyShort = 1_500_000;
    movements.push(
      {
        id: "-Nmove0000000000002",
        direction: "in",
        amount: ugx(40 * M),
        bankReference: "FT26317KX2Q4",
        valueDate: "2026-11-13",
        reason: "Second tranche agreed by the board",
        status: "proposed",
        proposedBy: BUFFER_COLLEAGUE.id,
        proposedAt: "2026-11-13T08:40:00Z",
        decidedBy: null,
        decidedAt: null,
        decisionReason: null,
      },
      {
        id: "-Nmove0000000000001",
        direction: "in",
        amount: ugx(120 * M),
        bankReference: "FT26310PL8M1",
        valueDate: "2026-11-06",
        reason: "Opening the buffer with Finvest's own money",
        status: "approved",
        proposedBy: BUFFER_COLLEAGUE.id,
        proposedAt: "2026-11-06T09:05:00Z",
        decidedBy: me.id,
        decidedAt: "2026-11-06T11:20:00Z",
        decisionReason: null,
      },
    );
    proposals.push({
      id: "-Nrules000000000001",
      rules: { effectiveFrom: "2026-12-01", target: ugx(200 * M), floor: ugx(120 * M), fromBufferUpTo: ugx(5 * M), payWithinWorkingDays: 2 },
      reason: "More early withdrawals expected over the holidays",
      status: "proposed",
      proposedBy: BUFFER_COLLEAGUE.id,
      proposedAt: "2026-11-12T14:10:00Z",
      decidedBy: null,
      decidedAt: null,
      decisionNote: null,
    });
  }

  const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(now());
  const replies = new Map<string, BufferScreen>();
  let counter = 0;
  const nextId = (prefix: string) => `-N${prefix}${String(++counter).padStart(14, "0")}`;

  const paidIn = () =>
    movements
      .filter((m) => m.status === "approved")
      .reduce((sum, m) => sum + (m.direction === "in" ? m.amount.minor : -m.amount.minor), 0);

  function inForce(): Rules {
    const day = today();
    return versions.filter((v) => v.effectiveFrom <= day).at(-1) ?? SEED;
  }

  function steps(proposedBy: string, waiting: boolean, deciding: BufferStep[], canWithdraw: boolean): BufferStep[] {
    if (!waiting) return [];
    if (proposedBy === me.id) return canWithdraw ? ["withdraw"] : [];
    return deciding;
  }

  function screen(currency: Currency): BufferScreen {
    const rules = inForce();
    const balance = paidIn() - clientMoneyShort;
    return {
      currency,
      buffer: ugx(balance),
      level: balance >= rules.target.minor ? "on_target" : balance >= rules.floor.minor ? "under_target" : "below_floor",
      inForce: rules,
      versions: [...versions],
      proposals: proposals.map((p) => ({ ...p, allowed: steps(p.proposedBy, p.status === "proposed", ["approve", "send_back"], true) })),
      finvestMoney: ugx(paidIn()),
      movements: movements.map((m) => ({ ...m, allowed: steps(m.proposedBy, m.status === "proposed", ["approve", "decline"], false) })),
    };
  }

  /** Answers a repeated key with the first answer, as the backend does. */
  async function once(key: string, command: () => void): Promise<BufferScreen> {
    const saved = replies.get(key);
    if (saved) return saved;
    command();
    const reply = screen("UGX");
    replies.set(key, reply);
    return Promise.resolve(reply);
  }

  function waitingProposal(id: string): Stored<RulesProposal> {
    const found = proposals.find((p) => p.id === id);
    if (!found) throw new BufferError("not_found");
    if (found.status !== "proposed") throw new BufferError("conflict");
    return found;
  }

  function waitingMovement(id: string): Stored<Movement> {
    const found = movements.find((m) => m.id === id);
    if (!found) throw new BufferError("not_found");
    if (found.status !== "proposed") throw new BufferError("conflict");
    if (found.proposedBy === me.id) throw new BufferError("forbidden");
    return found;
  }

  const decided = () => ({ decidedBy: me.id, decidedAt: now().toISOString() });

  return {
    getBuffer: (currency) => Promise.resolve(screen(currency)),
    proposeRules: (key, rules: NewRules) =>
      once(key, () => {
        if (rules.effectiveFrom <= today()) throw new BufferError("effective_date_not_in_future");
        if (versions.some((v) => v.effectiveFrom === rules.effectiveFrom)) throw new BufferError("date_already_taken");
        if (rules.floor.minor >= rules.target.minor || rules.fromBufferUpTo.minor > rules.floor.minor) {
          throw new BufferError("rules_inconsistent");
        }
        const { reason, ...values } = rules;
        proposals.unshift({
          id: nextId("rules"),
          rules: values,
          reason,
          status: "proposed",
          proposedBy: me.id,
          proposedAt: now().toISOString(),
          decidedBy: null,
          decidedAt: null,
          decisionNote: null,
        });
      }),
    approveRules: (key, id) =>
      once(key, () => {
        const proposal = waitingProposal(id);
        if (proposal.proposedBy === me.id) throw new BufferError("forbidden");
        Object.assign(proposal, { status: "approved", ...decided() });
        versions.push(proposal.rules);
        versions.sort((a, b) => a.effectiveFrom.localeCompare(b.effectiveFrom));
      }),
    sendBackRules: (key, id, note) =>
      once(key, () => {
        const proposal = waitingProposal(id);
        if (proposal.proposedBy === me.id) throw new BufferError("forbidden");
        Object.assign(proposal, { status: "sent_back", decisionNote: note, ...decided() });
      }),
    withdrawRules: (key, id) =>
      once(key, () => {
        const proposal = waitingProposal(id);
        if (proposal.proposedBy !== me.id) throw new BufferError("forbidden");
        Object.assign(proposal, { status: "withdrawn", ...decided() });
      }),
    proposeMovement: (key, given: NewMovement) =>
      once(key, () => {
        if (given.direction === "in") {
          if (given.valueDate > today()) throw new BufferError("value_date_in_future");
          if (movements.some((m) => m.bankReference === given.bankReference && m.status !== "declined")) {
            throw new BufferError("already_recorded");
          }
        } else {
          if (given.amount.minor > paidIn()) throw new BufferError("more_than_paid_in");
          if (given.amount.minor > paidIn() - clientMoneyShort) throw new BufferError("more_than_the_buffer");
        }
        movements.unshift({
          id: nextId("move"),
          direction: given.direction,
          amount: given.amount,
          bankReference: given.direction === "in" ? given.bankReference : null,
          valueDate: given.direction === "in" ? given.valueDate : null,
          reason: given.reason,
          status: "proposed",
          proposedBy: me.id,
          proposedAt: now().toISOString(),
          decidedBy: null,
          decidedAt: null,
          decisionReason: null,
        });
      }),
    approveMovement: (key, id) =>
      once(key, () => {
        const found = waitingMovement(id);
        if (found.direction === "out" && found.amount.minor > paidIn() - clientMoneyShort) {
          throw new BufferError("more_than_the_buffer");
        }
        Object.assign(found, { status: "approved", valueDate: found.valueDate ?? today(), ...decided() });
      }),
    declineMovement: (key, id, reason) =>
      once(key, () => {
        Object.assign(waitingMovement(id), { status: "declined", decisionReason: reason, ...decided() });
      }),
  };
}
