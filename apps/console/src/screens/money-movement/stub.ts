import { PayoutsError, type Amount, type HeldUpPayout, type PayoutsApi, type Resolution, type StrandedPayout } from "./api.ts";

/**
 * Sample payouts for `pnpm dev` and previews. Not real people or money, and not
 * connected to Finvest. Decisions change only this browser tab's copy. The rules
 * here stand in for the backend's: the screen itself decides nothing.
 */

export type StubCase = "sample" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const OTHER = "sample-colleague";

function ago(minutes: number, now: Date): string {
  return new Date(now.getTime() - minutes * 60_000).toISOString();
}

function sampleHeldUp(now: Date): HeldUpPayout[] {
  return [
    {
      id: "-OdPay0000000000001A",
      clientId: "-OdClient00000000001",
      amount: ugx(2_400_000),
      createdAt: ago(9 * 60, now),
      firstTriedAt: ago(9 * 60 - 1, now),
      lastTriedAt: ago(12, now),
      tries: 11,
    },
    {
      id: "-OdPay0000000000002B",
      clientId: "-OdClient00000000002",
      amount: ugx(350_000),
      createdAt: ago(150, now),
      firstTriedAt: ago(149, now),
      lastTriedAt: ago(4, now),
      tries: 6,
    },
  ];
}

function sampleStranded(now: Date, me: string): StrandedPayout[] {
  const proposal = (id: string, by: string, minutes: number): Resolution => ({
    id,
    outcome: "failed",
    statementLineId: null,
    statementId: "-OdStatement000000004",
    reason: "No ioTec line carries this payout on any day from the send to three days after it. Checked each statement.",
    proposedBy: by,
    proposedAt: ago(minutes, now),
  });
  return [
    {
      id: "-OdPay0000000000003C",
      clientId: "-OdClient00000000003",
      status: "uncertain",
      amount: ugx(1_200_000),
      firstSentAt: ago(6 * 24 * 60, now),
      resolution: null,
    },
    {
      id: "-OdPay0000000000004D",
      clientId: "-OdClient00000000004",
      status: "sent",
      amount: ugx(5_000_000),
      firstSentAt: ago(5 * 24 * 60, now),
      resolution: proposal("-OdResolution0000001", OTHER, 40),
    },
    {
      id: "-OdPay0000000000005E",
      clientId: "-OdClient00000000005",
      status: "sent",
      amount: ugx(780_000),
      firstSentAt: ago(4 * 24 * 60, now),
      resolution: { ...proposal("-OdResolution0000002", me, 15), outcome: "paid", statementId: null, statementLineId: "-OdLine00000000000077", reason: "The ioTec statement for the 9th shows it going out under its ID." },
    },
  ];
}

export function createStubPayoutsApi(me: string, delayMs = 150, sample: StubCase = "sample", now = () => new Date()): PayoutsApi {
  let heldUp = sample === "sample" ? sampleHeldUp(now()) : [];
  let stranded = sample === "sample" ? sampleStranded(now(), me) : [];
  let next = 1;

  const wait = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        if (sample === "error") reject(new PayoutsError("unavailable"));
        else {
          try {
            resolve(value());
          } catch (error) {
            reject(error);
          }
        }
      }, delayMs),
    );

  function find(resolutionId: string): StrandedPayout {
    const payout = stranded.find((item) => item.resolution?.id === resolutionId);
    if (!payout?.resolution) throw new PayoutsError("resolution_already_decided");
    return payout;
  }

  function settle(payoutId: string) {
    stranded = stranded.filter((item) => item.id !== payoutId);
    heldUp = heldUp.filter((item) => item.id !== payoutId);
  }

  function clear(payoutId: string) {
    stranded = stranded.map((item) => (item.id === payoutId ? { ...item, resolution: null } : item));
  }

  return {
    listHeldUp: () => wait(() => heldUp.map((item) => ({ ...item }))),
    listStranded: () => wait(() => stranded.map((item) => ({ ...item }))),
    propose: (_key, payoutId, proposal) =>
      wait(() => {
        const payout = stranded.find((item) => item.id === payoutId);
        if (!payout) throw new PayoutsError("payout_not_stranded");
        if (payout.resolution) throw new PayoutsError("resolution_already_proposed");
        if (!proposal.reason.trim()) throw new PayoutsError("invalid");
        const cited = proposal.outcome === "paid" ? proposal.statementLineId : proposal.statementId;
        if (!cited?.trim()) {
          throw new PayoutsError(proposal.outcome === "paid" ? "statement_line_required" : "statement_required");
        }
        payout.resolution = {
          id: `-OdResolutionNew${String(next++).padStart(4, "0")}`,
          outcome: proposal.outcome,
          statementLineId: proposal.outcome === "paid" ? cited.trim() : null,
          statementId: proposal.outcome === "failed" ? cited.trim() : null,
          reason: proposal.reason.trim(),
          proposedBy: me,
          proposedAt: now().toISOString(),
        };
      }),
    approve: (_key, resolutionId) =>
      wait(() => {
        const payout = find(resolutionId);
        if (payout.resolution?.proposedBy === me) throw new PayoutsError("proposed_by_you");
        settle(payout.id);
      }),
    decline: (resolutionId) =>
      wait(() => {
        const payout = find(resolutionId);
        if (payout.resolution?.proposedBy === me) throw new PayoutsError("proposed_by_you");
        clear(payout.id);
      }),
    withdraw: (resolutionId) =>
      wait(() => {
        const payout = find(resolutionId);
        if (payout.resolution?.proposedBy !== me) throw new PayoutsError("not_proposed_by_you");
        clear(payout.id);
      }),
  };
}
