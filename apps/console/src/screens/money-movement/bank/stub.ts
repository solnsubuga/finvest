import {
  BankPayoutsError,
  type Amount,
  type BankPayout,
  type BankPayoutsApi,
  type Batch,
  type Currency,
  type SentBankPayout,
  type Settlement,
} from "./api.ts";

/**
 * Sample bank payouts for `pnpm dev` and previews. Not real people or money, and not connected
 * to Finvest. Decisions change only this browser tab's copy. The rules here stand in for the
 * backend's: the screen itself decides nothing.
 */

export type BankStubCase = "sample" | "empty" | "error";

export const BANK_COLLEAGUE = "sample-colleague";

const money = (minor: number, currency: Currency = "UGX"): Amount => ({ minor, currency });

function ago(minutes: number, now: Date): string {
  return new Date(now.getTime() - minutes * 60_000).toISOString();
}

function payout(id: number, minor: number, minutes: number, now: Date, currency: Currency = "UGX"): BankPayout {
  const created = ago(minutes, now);
  return {
    id: `-OdBankPay00000000${String(id).padStart(2, "0")}`,
    clientId: `-OdClient000000000${String(id).padStart(2, "0")}`,
    destinationId: `-OdDest00000000000${String(id).padStart(2, "0")}`,
    amount: money(minor, currency),
    status: "queued",
    businessDate: created.slice(0, 10),
    createdAt: created,
  };
}

interface State {
  payouts: BankPayout[];
  batches: Batch[];
  sent: SentBankPayout[];
}

function sample(now: Date, me: string): State {
  const queued = [
    payout(1, 4_500_000, 26 * 60, now),
    payout(2, 1_250_000, 20 * 60, now),
    payout(3, 800_000, 5 * 60, now),
    payout(4, 12_000_000, 90, now),
    payout(5, 150_000, 60, now, "USD"),
  ];
  const inBatch = [payout(6, 3_000_000, 30 * 60, now), payout(7, 2_000_000, 29 * 60, now)];
  const sentOnes = [payout(8, 6_400_000, 3 * 24 * 60, now), payout(9, 950_000, 3 * 24 * 60, now), payout(10, 2_200_000, 2 * 24 * 60, now)].map(
    (each): BankPayout => ({ ...each, status: "sent" }),
  );
  const settlement = (by: string, of: BankPayout, minutes: number): Settlement => ({
    id: `-OdBankSettle${of.id.slice(-7)}`,
    payoutId: of.id,
    outcome: "paid",
    statementLineId: "-OdTrustLine00000017",
    statementId: null,
    reason: "Stanbic trust statement for yesterday shows it going out, same amount, our reference.",
    proposedBy: by,
    proposedAt: ago(minutes, now),
  });
  return {
    payouts: [...queued, ...inBatch, ...sentOnes],
    batches: [
      {
        id: "-OdBankBatch00000001",
        currency: "UGX",
        reason: "Tuesday afternoon bank payouts",
        status: "prepared",
        preparedBy: BANK_COLLEAGUE,
        preparedAt: ago(40, now),
        decidedBy: null,
        decidedAt: null,
        decisionReason: null,
        payoutIds: inBatch.map((each) => each.id),
        total: money(5_000_000),
      },
      {
        id: "-OdBankBatch00000002",
        currency: "UGX",
        reason: "Monday bank payouts",
        status: "approved",
        preparedBy: me,
        preparedAt: ago(3 * 24 * 60 + 30, now),
        decidedBy: BANK_COLLEAGUE,
        decidedAt: ago(3 * 24 * 60, now),
        decisionReason: "Checked each account against the client's approved destination",
        payoutIds: sentOnes.slice(0, 2).map((each) => each.id),
        total: money(7_350_000),
      },
    ],
    sent: [
      { payout: sentOnes[0]!, batchId: "-OdBankBatch00000002", sentAt: ago(3 * 24 * 60, now), settlement: settlement(BANK_COLLEAGUE, sentOnes[0]!, 50) },
      { payout: sentOnes[1]!, batchId: "-OdBankBatch00000002", sentAt: ago(3 * 24 * 60, now), settlement: null },
      { payout: sentOnes[2]!, batchId: "-OdBankBatch00000003", sentAt: ago(2 * 24 * 60, now), settlement: settlement(me, sentOnes[2]!, 15) },
    ],
  };
}

export function createStubBankPayoutsApi(me: string, delay = 150, which: BankStubCase = "sample", now = new Date()): BankPayoutsApi {
  const state: State = which === "sample" ? sample(now, me) : { payouts: [], batches: [], sent: [] };
  let made = 10;
  const wait = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        if (which === "error") {
          reject(new BankPayoutsError("unavailable"));
          return;
        }
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new BankPayoutsError("unavailable"));
        }
      }, delay),
    );
  const refuse = (code: ConstructorParameters<typeof BankPayoutsError>[0]): never => {
    throw new BankPayoutsError(code);
  };
  const inLiveBatch = (id: string) => state.batches.some((each) => (each.status === "prepared" || each.status === "approved") && each.payoutIds.includes(id));
  const openBatch = (id: string) => {
    const found = state.batches.find((each) => each.id === id) ?? refuse("not_found");
    if (found.status !== "prepared") refuse("batch_already_decided");
    return found;
  };
  const decide = (id: string, status: Batch["status"], reason: string) => {
    const found = openBatch(id);
    Object.assign(found, { status, decidedBy: me, decidedAt: new Date().toISOString(), decisionReason: reason });
    return found;
  };
  const sentOne = (id: string) => state.sent.find((each) => each.settlement?.id === id) ?? refuse("not_found");

  return {
    listWaiting: (currency) =>
      wait(() => state.payouts.filter((each) => each.status === "queued" && each.amount.currency === currency && !inLiveBatch(each.id))),
    listBatches: () =>
      wait(() => state.batches.filter((each) => each.status === "prepared" || each.status === "approved").sort((a, b) => b.preparedAt.localeCompare(a.preparedAt))),
    prepare: (_key, currency, payoutIds, reason) =>
      wait(() => {
        if (payoutIds.length === 0 || payoutIds.length > 200) refuse("batch_payouts_invalid");
        const chosen = payoutIds.map((id) => state.payouts.find((each) => each.id === id) ?? refuse("payout_not_bank"));
        for (const each of chosen) {
          if (each.amount.currency !== currency) refuse("currency_not_supported");
          if (each.status !== "queued") refuse("payout_not_queued");
          if (inLiveBatch(each.id)) refuse("payout_in_another_batch");
        }
        made += 1;
        state.batches.push({
          id: `-OdBankBatch000000${String(made).padStart(2, "0")}`,
          currency,
          reason,
          status: "prepared",
          preparedBy: me,
          preparedAt: new Date().toISOString(),
          decidedBy: null,
          decidedAt: null,
          decisionReason: null,
          payoutIds,
          total: money(chosen.reduce((sum, each) => sum + each.amount.minor, 0), currency),
        });
      }),
    approve: (_key, id, reason) =>
      wait(() => {
        if (openBatch(id).preparedBy === me) refuse("prepared_by_you");
        const batch = decide(id, "approved", reason);
        for (const payoutId of batch.payoutIds) {
          const found = state.payouts.find((each) => each.id === payoutId);
          if (!found) continue;
          found.status = "sent";
          state.sent.push({ payout: found, batchId: batch.id, sentAt: new Date().toISOString(), settlement: null });
        }
      }),
    decline: (_key, id, reason) =>
      wait(() => {
        if (openBatch(id).preparedBy === me) refuse("prepared_by_you");
        decide(id, "declined", reason);
      }),
    withdraw: (id, reason) =>
      wait(() => {
        if (openBatch(id).preparedBy !== me) refuse("not_prepared_by_you");
        decide(id, "withdrawn", reason);
      }),
    downloadFile: (id) =>
      wait(() => {
        const batch = state.batches.find((each) => each.id === id) ?? refuse("not_found");
        if (batch.status !== "approved") refuse("batch_not_approved");
        const rows = batch.payoutIds.map((payoutId) => `${payoutId},Sample client,Sample Bank,0000000000,0,${batch.currency},Finvest payout`);
        return new Blob([["Reference,Beneficiary name,Bank,Account number,Amount,Currency,Narrative", ...rows].join("\r\n")], { type: "text/csv" });
      }),
    listSent: () => wait(() => state.sent.filter((each) => each.payout.status === "sent")),
    propose: (_key, payoutId, proposal) =>
      wait(() => {
        const found = state.sent.find((each) => each.payout.id === payoutId && each.payout.status === "sent") ?? refuse("payout_not_sent");
        if (found.settlement) refuse("settlement_already_proposed");
        const evidence = proposal.outcome === "paid" ? proposal.statementLineId : proposal.statementId;
        if (!evidence) refuse(proposal.outcome === "paid" ? "statement_line_required" : "statement_required");
        made += 1;
        found.settlement = {
          id: `-OdBankSettle0000${String(made).padStart(3, "0")}`,
          payoutId,
          outcome: proposal.outcome,
          statementLineId: proposal.outcome === "paid" ? (evidence ?? null) : null,
          statementId: proposal.outcome === "failed" ? (evidence ?? null) : null,
          reason: proposal.reason,
          proposedBy: me,
          proposedAt: new Date().toISOString(),
        };
      }),
    approveSettlement: (_key, id) =>
      wait(() => {
        const found = sentOne(id);
        if (found.settlement?.proposedBy === me) refuse("proposed_by_you");
        found.payout.status = found.settlement?.outcome ?? "paid";
      }),
    declineSettlement: (id) =>
      wait(() => {
        const found = sentOne(id);
        if (found.settlement?.proposedBy === me) refuse("proposed_by_you");
        found.settlement = null;
      }),
    withdrawSettlement: (id) =>
      wait(() => {
        const found = sentOne(id);
        if (found.settlement?.proposedBy !== me) refuse("not_proposed_by_you");
        found.settlement = null;
      }),
  };
}
