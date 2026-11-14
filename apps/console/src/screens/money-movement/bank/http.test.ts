import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { bankPayoutsErrorMessage } from "./api.ts";
import { createHttpBankPayoutsApi } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string, field?: string) {
  const fields = field ? [{ field_path: ["body"], code: field }] : [];
  return reply(status, { request_id: "req", error: { code, message: code, fields } });
}

const PAYOUT = {
  id: "-NpayoutA00000000001",
  client_id: "-Nclient00000000000A",
  destination_id: "-NdestA0000000000001",
  amount: { minor: 2_500_000, currency: "UGX" },
  status: "sent",
  business_date: "2026-11-13",
  created_at: "2026-11-13T06:00:00Z",
};

const SETTLEMENT = {
  id: "-NsettleA00000000001",
  payout_id: PAYOUT.id,
  batch_id: "-NbatchA000000000001",
  outcome: "paid",
  statement_line_id: "-NlineA0000000000001",
  statement_id: null,
  reason: "On the trust statement",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-14T08:00:00Z",
  decided_by: null,
  decided_at: null,
  decision_reason: null,
};

const body = (init: RequestInit | undefined) => JSON.parse(String(init?.body)) as Record<string, unknown>;
const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string>)[name];
const api = (fetch: typeof globalThis.fetch) => createHttpBankPayoutsApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));

describe("bank payouts over HTTP", () => {
  it("reads the payouts waiting in a currency", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [{ ...PAYOUT, status: "queued" }]));
    const [item] = await api(fetch).listWaiting("USD");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/payouts/bank/waiting?currency=USD");
    expect(item).toEqual({
      id: PAYOUT.id,
      clientId: PAYOUT.client_id,
      destinationId: PAYOUT.destination_id,
      amount: PAYOUT.amount,
      status: "queued",
      businessDate: "2026-11-13",
      createdAt: "2026-11-13T06:00:00Z",
    });
  });

  it("reads sent payouts with only an open settlement", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(200, [
        { payout: PAYOUT, batch_id: SETTLEMENT.batch_id, sent_at: "2026-11-13T09:00:00Z", settlement: SETTLEMENT },
        { payout: PAYOUT, batch_id: SETTLEMENT.batch_id, sent_at: "2026-11-13T09:00:00Z", settlement: { ...SETTLEMENT, status: "declined" } },
      ]),
    );
    const [open, declined] = await api(fetch).listSent();
    expect(open?.settlement).toMatchObject({ id: SETTLEMENT.id, outcome: "paid", statementLineId: "-NlineA0000000000001", proposedBy: "-Nstaff000000000000B" });
    expect(declined?.settlement).toBeNull();
  });

  it("reads a batch with its payouts, accounts masked", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(200, [
        {
          id: "-NbatchA000000000001",
          currency: "UGX",
          reason: "Today",
          status: "prepared",
          prepared_by: "-Nstaff000000000000B",
          prepared_at: "2026-11-14T08:00:00Z",
          decided_by: null,
          decided_at: null,
          decision_reason: null,
          payout_ids: [PAYOUT.id],
          total: PAYOUT.amount,
          items: [{ payout_id: PAYOUT.id, client_id: PAYOUT.client_id, amount: PAYOUT.amount, bank_name: "Stanbic Bank", masked_account: "•••• 4821", holder_name: "Sarah Nakato" }],
        },
      ]),
    );
    const [batch] = await api(fetch).listBatches();
    expect(batch?.items).toEqual([
      { payoutId: PAYOUT.id, clientId: PAYOUT.client_id, amount: PAYOUT.amount, bankName: "Stanbic Bank", maskedAccount: "•••• 4821", holderName: "Sarah Nakato" },
    ]);
  });

  it("prepares a batch with an idempotency key", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, {}));
    await api(fetch).prepare("key-1", "UGX", [PAYOUT.id], "Wednesday");
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe("https://api.test/staff/payout-bank-batches");
    expect(body(init)).toEqual({ currency: "UGX", payout_ids: [PAYOUT.id], reason: "Wednesday" });
    expect(header(init, "Idempotency-Key")).toBe("key-1");
  });

  it("decides a batch: approve and decline with a key, withdraw without", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, {}));
    const bank = api(fetch);
    await bank.approve("key-2", "-NbatchA000000000001", "Checked");
    await bank.decline("key-3", "-NbatchA000000000001", "Wrong account");
    await bank.withdraw("-NbatchA000000000001", "Not today");
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "https://api.test/staff/payout-bank-batches/-NbatchA000000000001/approve",
      "https://api.test/staff/payout-bank-batches/-NbatchA000000000001/decline",
      "https://api.test/staff/payout-bank-batches/-NbatchA000000000001/withdraw",
    ]);
    expect(fetch.mock.calls.map((call) => header(call[1], "Idempotency-Key"))).toEqual(["key-2", "key-3", undefined]);
  });

  it("downloads the transfer file", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => Promise.resolve(new Response("Reference,Amount\r\n", { status: 200, headers: { "Content-Type": "text/csv" } })));
    const file = await api(fetch).downloadFile("-NbatchA000000000001");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/payout-bank-batches/-NbatchA000000000001/file");
    expect(await file.text()).toBe("Reference,Amount\r\n");
  });

  it("proposes a settlement with only the evidence its outcome needs", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, SETTLEMENT));
    await api(fetch).propose("key-4", PAYOUT.id, { outcome: "failed", statementId: "-NstatementA00000001", statementLineId: "-Nx", reason: "No line" });
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe(`https://api.test/staff/payouts/${PAYOUT.id}/bank-settlements`);
    expect(body(init)).toEqual({ outcome: "failed", statement_line_id: null, statement_id: "-NstatementA00000001", reason: "No line" });
    expect(header(init, "Idempotency-Key")).toBe("key-4");
  });

  it("approves a settlement with a key, and declines or withdraws without one", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, {}));
    const bank = api(fetch);
    await bank.approveSettlement("key-5", SETTLEMENT.id, "Opened it");
    await bank.declineSettlement(SETTLEMENT.id, "Wrong line");
    await bank.withdrawSettlement(SETTLEMENT.id, "Wrong line");
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      `https://api.test/staff/payout-bank-settlements/${SETTLEMENT.id}/approve`,
      `https://api.test/staff/payout-bank-settlements/${SETTLEMENT.id}/decline`,
      `https://api.test/staff/payout-bank-settlements/${SETTLEMENT.id}/withdraw`,
    ]);
    expect(fetch.mock.calls.map((call) => header(call[1], "Idempotency-Key"))).toEqual(["key-5", undefined, undefined]);
  });

  it("words the backend's named refusals", async () => {
    const cases: [Promise<Response>, string][] = [
      [refusal(409, "conflict", "payout_in_another_batch"), "already in another batch"],
      [refusal(403, "forbidden", "prepared_by_you"), "You prepared this batch"],
      [refusal(409, "conflict", "statement_not_enough"), "That statement does not show the payout never left."],
      [refusal(403, "confirm_it_is_you"), "you did not confirm it is you"],
      [refusal(409, "conflict", "something_new"), "Finvest refused this as things now stand."],
      [refusal(500, "internal"), "We could not reach Finvest."],
    ];
    for (const [response, words] of cases) {
      const fetch = vi.fn<typeof globalThis.fetch>(() => response);
      const error = await api(fetch).approve("key", "-NbatchA000000000001", "Checked").catch((caught: unknown) => caught);
      expect(bankPayoutsErrorMessage(error)).toContain(words);
    }
  });

  it("reads a batch's bulk debit and sends a proposal with its key", async () => {
    const batchJson = {
      id: "-NbatchA000000000001",
      currency: "UGX",
      reason: "Tuesday",
      status: "approved",
      prepared_by: "-Nstaff000000000000A",
      prepared_at: "2026-11-13T06:00:00Z",
      decided_by: "-Nstaff000000000000B",
      decided_at: "2026-11-13T07:00:00Z",
      decision_reason: "Checked",
      payout_ids: [PAYOUT.id],
      total: PAYOUT.amount,
      items: null,
    };
    const line = { id: "-NlineA0000000000001", value_date: "2026-11-13", amount: PAYOUT.amount, reference: "BULK 1", narrative: "Bulk transfer" };
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { batch: batchJson, payouts: [PAYOUT], lines: [line] }))
      .mockImplementationOnce(() => reply(201, {}))
      .mockImplementationOnce(() => refusal(409, "conflict", "amounts_do_not_add_up"));
    const debit = await api(fetch).getBulkDebit(batchJson.id);
    expect(fetch.mock.calls[0]?.[0]).toBe(`https://api.test/staff/payout-bank-batches/${batchJson.id}/bulk-debit`);
    expect(debit.lines).toEqual([{ id: line.id, valueDate: "2026-11-13", amount: PAYOUT.amount, reference: "BULK 1", narrative: "Bulk transfer" }]);
    expect(debit.payouts.map((each) => each.id)).toEqual([PAYOUT.id]);

    await api(fetch).proposeBulk("key-1", batchJson.id, line.id, [PAYOUT.id], "One debit");
    const [url, init] = fetch.mock.calls[1]!;
    expect(url).toBe(`https://api.test/staff/payout-bank-batches/${batchJson.id}/bulk-settlements`);
    expect(body(init)).toEqual({ statement_line_id: line.id, payout_ids: [PAYOUT.id], reason: "One debit" });
    expect(header(init, "Idempotency-Key")).toBe("key-1");

    const error = await api(fetch).proposeBulk("key-2", batchJson.id, line.id, [PAYOUT.id], "One debit").catch((caught: unknown) => caught);
    expect(bankPayoutsErrorMessage(error)).toMatch(/do not add up to the line/);
  });

  it("reads a waiting bulk debit with its statement line", async () => {
    const line = { id: "-NlineA0000000000001", value_date: "2026-11-13", amount: PAYOUT.amount, reference: "BULK 1", narrative: "Bulk transfer" };
    const bulk = {
      id: "-NbulkA0000000000001",
      batch_id: "-NbatchA000000000001",
      statement_line_id: line.id,
      reason: "One debit",
      status: "proposed",
      proposed_by: "-NstaffA000000000001",
      proposed_at: "2026-11-13T08:00:00Z",
      decided_by: null,
      decided_at: null,
      decision_reason: null,
      payout_ids: [PAYOUT.id],
      total: PAYOUT.amount,
    };
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [{ ...bulk, line }, { ...bulk, id: "-NbulkA0000000000002", line: null }]));
    const [withLine, without] = await api(fetch).listBulks();
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/payout-bank-bulk-settlements");
    expect(withLine?.line).toEqual({ id: line.id, valueDate: "2026-11-13", amount: PAYOUT.amount, reference: "BULK 1", narrative: "Bulk transfer" });
    expect(without?.line).toBeNull();
  });

  it("marks a payout's settlement that belongs to a bulk debit", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(200, [{ payout: PAYOUT, batch_id: "-NbatchA000000000001", sent_at: "2026-11-13T07:00:00Z", settlement: { ...SETTLEMENT, bulk_id: "-NbulkA0000000000001" } }]),
    );
    const [item] = await api(fetch).listSent();
    expect(item?.settlement?.bulkId).toBe("-NbulkA0000000000001");
  });
});
