import type { Amount } from "../../products/api.ts";
import { CorrectionsError, type Correction, type CorrectionsApi, type NewCorrection } from "./api.ts";

/**
 * Sample corrections for `pnpm dev` and previews. Not real clients or money.
 * It keeps the backend's rules that a screen can show: the proposer never
 * decides, one open correction of a kind per investment, and a refund names
 * a payout account. Approving one finishes it straight away, as the worker
 * would a moment later.
 */

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const OTHER = "sample-staff-grace";

/** The sample investments the stub knows, by ID, with what each holds. */
const INVESTMENTS: Record<string, { client: string; principal: Amount; running: boolean }> = {
  "-InvSample00000000A1": { client: "-ClientSample0000001", principal: ugx(5_000_000), running: true },
  "-InvSample00000000A3": { client: "sample-client-amara", principal: ugx(1_000_000), running: true },
  "-InvSample00000000C1": { client: "sample-client-peter", principal: ugx(500_000), running: false },
};

function samples(me: string): Correction[] {
  const base = {
    destinationId: null,
    decidedBy: null,
    decidedAt: null,
    decisionNote: null,
    doneAt: null,
    payoutId: null,
    failureCode: null,
  };
  return [
    {
      ...base,
      id: "-CorrSample000000001",
      kind: "unwind",
      investmentId: "-InvSample00000000A3",
      clientId: "sample-client-amara",
      amount: ugx(1_000_000),
      reason: "Bank transfer FT2611130042 was matched to the wrong client. Payments is reversing the credit.",
      status: "proposed",
      proposedBy: OTHER,
      proposedAt: "2026-11-13T09:12:00Z",
    },
    {
      ...base,
      id: "-CorrSample000000002",
      kind: "refund",
      investmentId: "-InvSample00000000C1",
      clientId: "sample-client-peter",
      amount: ugx(500_000),
      destinationId: "-DestSample000000008",
      reason: "The client paid after the payment window closed and asked for the money back.",
      status: "proposed",
      proposedBy: me,
      proposedAt: "2026-11-13T10:40:00Z",
    },
    {
      ...base,
      id: "-CorrSample000000003",
      kind: "refund",
      investmentId: "-InvSample00000000C2",
      clientId: "sample-client-david",
      amount: ugx(250_000),
      destinationId: "-DestSample000000009",
      reason: "Paid twice for the same investment by mistake.",
      status: "done",
      proposedBy: OTHER,
      proposedAt: "2026-11-12T08:05:00Z",
      decidedBy: me,
      decidedAt: "2026-11-12T08:30:00Z",
      doneAt: "2026-11-12T08:30:04Z",
      payoutId: "-PayoutSample0000001",
    },
    {
      ...base,
      id: "-CorrSample000000004",
      kind: "refund",
      investmentId: "-InvSample00000000C3",
      clientId: "sample-client-joan",
      amount: ugx(9_000_000),
      destinationId: "-DestSample000000010",
      reason: "Investment cancelled at the client's request before it started.",
      status: "failed",
      proposedBy: me,
      proposedAt: "2026-11-11T14:00:00Z",
      decidedBy: OTHER,
      decidedAt: "2026-11-11T15:10:00Z",
      doneAt: "2026-11-11T15:10:03Z",
      failureCode: "over_mobile_money_limit",
    },
    {
      ...base,
      id: "-CorrSample000000005",
      kind: "unwind",
      investmentId: "-InvSample00000000A2",
      clientId: "-ClientSample0000002",
      amount: ugx(12_000_000),
      reason: "Thought the deposit was credited twice.",
      status: "sent_back",
      proposedBy: OTHER,
      proposedAt: "2026-11-10T11:00:00Z",
      decidedBy: me,
      decidedAt: "2026-11-10T11:45:00Z",
      decisionNote: "The bank statement shows two separate transfers. Nothing to undo.",
    },
  ];
}

export function createStubCorrectionsApi(me: string, sample: "default" | "empty" = "default", now = () => new Date().toISOString()): CorrectionsApi {
  let items = sample === "empty" ? [] : samples(me);
  const done = new Map<string, Correction>();
  const find = (id: string) => {
    const found = items.find((item) => item.id === id);
    if (!found) throw new CorrectionsError("not_found");
    return found;
  };
  const replace = (next: Correction) => {
    items = items.map((item) => (item.id === next.id ? next : item));
    return next;
  };
  // A key used again returns what it did the first time, as the API does.
  const once = async (key: string, run: () => Correction) => {
    const seen = done.get(key);
    if (seen) return seen;
    const result = run();
    done.set(key, result);
    return Promise.resolve(result);
  };
  const decide = (id: string, step: (item: Correction) => Correction) => {
    const item = find(id);
    if (item.status !== "proposed") throw new CorrectionsError("conflict");
    return replace(step(item));
  };

  return {
    list: () => Promise.resolve([...items].sort((a, b) => b.proposedAt.localeCompare(a.proposedAt))),
    propose: (key, input: NewCorrection) =>
      once(key, () => {
        const investment = INVESTMENTS[input.investmentId];
        if (!investment) throw new CorrectionsError("not_found");
        if (input.kind === "refund" && !input.destinationId) {
          throw new CorrectionsError("invalid", [{ field: "destination_id", code: "required" }]);
        }
        if (input.kind === "unwind" ? !investment.running : investment.running) throw new CorrectionsError("conflict");
        const open = items.some(
          (item) => item.investmentId === input.investmentId && item.kind === input.kind && (item.status === "proposed" || item.status === "approved"),
        );
        if (open) throw new CorrectionsError("conflict");
        const created: Correction = {
          id: `-CorrSample${String(items.length + 1).padStart(9, "0")}`,
          kind: input.kind,
          investmentId: input.investmentId,
          clientId: investment.client,
          amount: investment.principal,
          destinationId: input.kind === "refund" ? input.destinationId : null,
          reason: input.reason.trim(),
          status: "proposed",
          proposedBy: me,
          proposedAt: now(),
          decidedBy: null,
          decidedAt: null,
          decisionNote: null,
          doneAt: null,
          payoutId: null,
          failureCode: null,
        };
        items = [created, ...items];
        return created;
      }),
    approve: (key, id) =>
      once(key, () =>
        decide(id, (item) => {
          if (item.proposedBy === me) throw new CorrectionsError("same_person");
          const at = now();
          return {
            ...item,
            status: "done",
            decidedBy: me,
            decidedAt: at,
            doneAt: at,
            payoutId: item.kind === "refund" ? "-PayoutSample0000002" : null,
          };
        }),
      ),
    sendBack: (key, id, note) =>
      once(key, () =>
        decide(id, (item) => {
          if (item.proposedBy === me) throw new CorrectionsError("same_person");
          return { ...item, status: "sent_back", decidedBy: me, decidedAt: now(), decisionNote: note.trim() };
        }),
      ),
    withdraw: (key, id) =>
      once(key, () =>
        decide(id, (item) => {
          if (item.proposedBy !== me) throw new CorrectionsError("forbidden");
          return { ...item, status: "withdrawn", decidedBy: me, decidedAt: now() };
        }),
      ),
  };
}
