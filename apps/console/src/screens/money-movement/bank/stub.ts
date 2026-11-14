import { SAMPLE_CLIENTS } from "@/directory/stub.ts";
import {
  BankPayoutsError,
  type BatchItem,
  type Amount,
  type BankPayout,
  type BankPayoutsApi,
  type Batch,
  type BulkSettlement,
  type Currency,
  type StatementLine,
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

const BANKS = ["Stanbic Bank", "Centenary Bank", "Absa Bank Uganda", "dfcu Bank"];

function payout(id: number, minor: number, minutes: number, now: Date, currency: Currency = "UGX"): BankPayout {
  const created = ago(minutes, now);
  const client = SAMPLE_CLIENTS[id % SAMPLE_CLIENTS.length]!;
  return {
    id: `-OdBankPay00000000${String(id).padStart(2, "0")}`,
    clientId: client.clientId,
    destinationId: `-OdDest00000000000${String(id).padStart(2, "0")}`,
    amount: money(minor, currency),
    status: "queued",
    businessDate: created.slice(0, 10),
    createdAt: created,
  };
}

/** The masked account each sample payout goes to, as the backend gives it for a batch. */
function itemOf(of: BankPayout): BatchItem {
  const n = Number(of.id.slice(-2));
  return {
    payoutId: of.id,
    clientId: of.clientId,
    amount: of.amount,
    bankName: BANKS[n % BANKS.length] ?? null,
    maskedAccount: `•••• ${String(4000 + n * 137).slice(-4)}`,
    holderName: SAMPLE_CLIENTS.find((client) => client.clientId === of.clientId)?.name ?? "Sample client",
  };
}

interface State {
  payouts: BankPayout[];
  batches: Batch[];
  sent: SentBankPayout[];
  /** Trust statement lines going out, for the bulk debit picker. */
  lines: StatementLine[];
  bulks: BulkSettlement[];
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
    bulkId: null,
  });
  // A batch the bank took in one debit, still to settle, and another whose debit a colleague
  // has already proposed.
  const bulkOnes = [payout(11, 5_000_000, 2 * 24 * 60, now), payout(12, 3_250_000, 2 * 24 * 60, now), payout(13, 1_750_000, 2 * 24 * 60, now)].map(
    (each): BankPayout => ({ ...each, status: "sent" }),
  );
  const proposedOnes = [payout(14, 2_400_000, 4 * 24 * 60, now), payout(15, 600_000, 4 * 24 * 60, now)].map(
    (each): BankPayout => ({ ...each, status: "sent" }),
  );
  const yesterday = ago(24 * 60, now).slice(0, 10);
  const approvedBatch = (id: string, payouts: BankPayout[], minutes: number, reason: string): Batch => ({
    id,
    currency: "UGX",
    reason,
    status: "approved",
    preparedBy: me,
    preparedAt: ago(minutes + 30, now),
    decidedBy: BANK_COLLEAGUE,
    decidedAt: ago(minutes, now),
    decisionReason: "Checked each account against the client's approved destination",
    payoutIds: payouts.map((each) => each.id),
    total: money(payouts.reduce((sum, each) => sum + each.amount.minor, 0)),
    items: null,
  });
  const bulkId = "-OdBankBulk000000001";
  return {
    lines: [
      { id: "-OdTrustLine00000031", valueDate: yesterday, amount: money(10_000_000), reference: "BULK 0412 FINVEST", narrative: "Bulk transfer, 3 items" },
      { id: "-OdTrustLine00000032", valueDate: yesterday, amount: money(250_000), reference: "CHG 0412", narrative: "Bank charges" },
      { id: "-OdTrustLine00000033", valueDate: yesterday, amount: money(3_250_000), reference: "TRF 88123", narrative: "Transfer" },
    ],
    bulks: [
      {
        id: bulkId,
        batchId: "-OdBankBatch00000005",
        statementLineId: "-OdTrustLine00000027",
        reason: "Stanbic statement shows one debit for the whole file, BULK 0409 FINVEST.",
        status: "proposed",
        proposedBy: BANK_COLLEAGUE,
        proposedAt: ago(35, now),
        payoutIds: proposedOnes.map((each) => each.id),
        total: money(3_000_000),
      },
    ],
    payouts: [...queued, ...inBatch, ...sentOnes, ...bulkOnes, ...proposedOnes],
    batches: [
      {
        id: "-OdBankBatch00000001",
        currency: "UGX",
        reason: "Bank payouts asked for since this morning",
        status: "prepared",
        preparedBy: BANK_COLLEAGUE,
        preparedAt: ago(40, now),
        decidedBy: null,
        decidedAt: null,
        decisionReason: null,
        payoutIds: inBatch.map((each) => each.id),
        total: money(5_000_000),
        items: null,
      },
      {
        id: "-OdBankBatch00000002",
        currency: "UGX",
        reason: "Bank payouts from the start of the week",
        status: "approved",
        preparedBy: me,
        preparedAt: ago(3 * 24 * 60 + 30, now),
        decidedBy: BANK_COLLEAGUE,
        decidedAt: ago(3 * 24 * 60, now),
        decisionReason: "Checked each account against the client's approved destination",
        payoutIds: sentOnes.slice(0, 2).map((each) => each.id),
        total: money(7_350_000),
        items: null,
      },
      approvedBatch("-OdBankBatch00000004", bulkOnes, 2 * 24 * 60, "Bank payouts asked for on Tuesday"),
      approvedBatch("-OdBankBatch00000005", proposedOnes, 4 * 24 * 60, "Bank payouts from last week"),
    ],
    sent: [
      { payout: sentOnes[0]!, batchId: "-OdBankBatch00000002", sentAt: ago(3 * 24 * 60, now), settlement: settlement(BANK_COLLEAGUE, sentOnes[0]!, 50) },
      { payout: sentOnes[1]!, batchId: "-OdBankBatch00000002", sentAt: ago(3 * 24 * 60, now), settlement: null },
      { payout: sentOnes[2]!, batchId: "-OdBankBatch00000003", sentAt: ago(2 * 24 * 60, now), settlement: settlement(me, sentOnes[2]!, 15) },
      ...bulkOnes.map((each) => ({ payout: each, batchId: "-OdBankBatch00000004", sentAt: ago(2 * 24 * 60, now), settlement: null })),
      ...proposedOnes.map((each) => ({
        payout: each,
        batchId: "-OdBankBatch00000005",
        sentAt: ago(4 * 24 * 60, now),
        settlement: {
          ...settlement(BANK_COLLEAGUE, each, 35),
          statementLineId: "-OdTrustLine00000027",
          reason: "Stanbic statement shows one debit for the whole file, BULK 0409 FINVEST.",
          bulkId,
        },
      })),
    ],
  };
}

export function createStubBankPayoutsApi(me: string, delay = 150, which: BankStubCase = "sample", now = new Date()): BankPayoutsApi {
  const state: State = which === "sample" ? sample(now, me) : { payouts: [], batches: [], sent: [], lines: [], bulks: [] };
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
  const sentOne = (id: string) => {
    const found = state.sent.find((each) => each.settlement?.id === id) ?? refuse("not_found");
    if (found.settlement?.bulkId) refuse("settlement_in_bulk");
    return found;
  };
  const openBulk = (id: string) => {
    const found = state.bulks.find((each) => each.id === id) ?? refuse("not_found");
    if (found.status !== "proposed") refuse("settlement_already_decided");
    return found;
  };
  const lineInUse = (id: string) =>
    state.bulks.some((each) => each.statementLineId === id && (each.status === "proposed" || each.status === "approved")) ||
    state.sent.some((each) => each.settlement?.statementLineId === id);
  const toSettle = (batchId: string) => state.sent.filter((each) => each.batchId === batchId && each.payout.status === "sent" && !each.settlement);
  const closeBulk = (id: string, status: BulkSettlement["status"]) => {
    const found = openBulk(id);
    found.status = status;
    for (const each of state.sent) {
      if (each.settlement?.bulkId !== id) continue;
      if (status === "approved") each.payout.status = "paid";
      else each.settlement = null;
    }
  };

  return {
    listWaiting: (currency) =>
      wait(() => state.payouts.filter((each) => each.status === "queued" && each.amount.currency === currency && !inLiveBatch(each.id))),
    listBatches: () =>
      wait(() =>
        state.batches
          .filter((each) => each.status === "prepared" || each.status === "approved")
          .sort((a, b) => b.preparedAt.localeCompare(a.preparedAt))
          .map((each) => ({
            ...each,
            items:
              each.status === "prepared"
                ? each.payoutIds.flatMap((id) => {
                    const found = state.payouts.find((one) => one.id === id);
                    return found ? [itemOf(found)] : [];
                  })
                : null,
          })),
      ),
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
          items: null,
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
          bulkId: null,
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
    getBulkDebit: (batchId) =>
      wait(() => {
        const batch = state.batches.find((each) => each.id === batchId) ?? refuse("not_found");
        if (batch.status !== "approved") refuse("batch_not_approved");
        const payouts = toSettle(batchId).map((each) => each.payout);
        const owed = payouts.reduce((sum, each) => sum + each.amount.minor, 0);
        return {
          batch,
          payouts,
          lines: state.lines.filter((line) => line.amount.currency === batch.currency && line.amount.minor <= owed && !lineInUse(line.id)),
        };
      }),
    listBulks: () => wait(() => state.bulks.filter((each) => each.status === "proposed")),
    proposeBulk: (_key, batchId, statementLineId, payoutIds, reason) =>
      wait(() => {
        if (payoutIds.length === 0 || new Set(payoutIds).size !== payoutIds.length) refuse("bulk_payouts_invalid");
        const free = toSettle(batchId);
        const chosen = payoutIds.map((id) => free.find((each) => each.payout.id === id) ?? refuse("payout_not_to_settle"));
        const line = state.lines.find((each) => each.id === statementLineId) ?? refuse("statement_line_not_this_batch");
        if (lineInUse(line.id)) refuse("statement_line_in_use");
        const total = chosen.reduce((sum, each) => sum + each.payout.amount.minor, 0);
        if (total !== line.amount.minor) refuse("amounts_do_not_add_up");
        made += 1;
        const id = `-OdBankBulk0000000${String(made).padStart(2, "0")}`;
        const proposedAt = new Date().toISOString();
        state.bulks.push({ id, batchId, statementLineId, reason, status: "proposed", proposedBy: me, proposedAt, payoutIds, total: line.amount });
        for (const each of chosen) {
          each.settlement = {
            id: `-OdBankSettle${each.payout.id.slice(-7)}`,
            payoutId: each.payout.id,
            outcome: "paid",
            statementLineId,
            statementId: null,
            reason,
            proposedBy: me,
            proposedAt,
            bulkId: id,
          };
        }
      }),
    approveBulk: (_key, id) =>
      wait(() => {
        if (openBulk(id).proposedBy === me) refuse("proposed_by_you");
        closeBulk(id, "approved");
      }),
    declineBulk: (id) =>
      wait(() => {
        if (openBulk(id).proposedBy === me) refuse("proposed_by_you");
        closeBulk(id, "declined");
      }),
    withdrawBulk: (id) =>
      wait(() => {
        if (openBulk(id).proposedBy !== me) refuse("not_proposed_by_you");
        closeBulk(id, "withdrawn");
      }),
  };
}
