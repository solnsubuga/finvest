import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { bankTransferErrorMessage } from "./api.ts";
import { createHttpBankTransfersApi } from "./http.ts";

const ME = "-Nstaff000000000000A";
const OTHER = "-Nstaff000000000000B";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string, field?: string) {
  const fields = field ? [{ field_path: ["statement_line_id"], code: field }] : [];
  return reply(status, { request_id: "req", error: { code, message: code, fields } });
}

const PAYMENT = {
  id: "-Npayment0000000000A",
  investment_id: "-Ninvestment00000000A",
  method: "bank_transfer",
  status: "in_review",
  amount: { minor: 2_500_000, currency: "UGX" },
  failure: null,
  payer: null,
  receipt: {
    sent_on: "2026-11-12",
    bank_name: "Stanbic Bank",
    transaction_number: "FT2631600123",
    content_type: "application/pdf",
    byte_size: 120_000,
  },
  created_at: "2026-11-12T10:00:00Z",
  confirmed_at: null,
};

const LINE = {
  id: "-Nline0000000000000A",
  account_reference: "9030012345678",
  value_date: "2026-11-12",
  amount: { minor: 2_500_000, currency: "UGX" },
  direction: "in",
  reference: "FT2631600123",
  narrative: "TRANSFER FROM A CLIENT",
};

function transfer(review: Record<string, unknown> | null, status = "in_review") {
  return {
    payment: { ...PAYMENT, status },
    client_id: "-Nclient00000000000A",
    receipt_link: { url: "https://files.test/receipt", expires_at: "2026-11-14T10:05:00Z" },
    review,
    matched_line: review ? LINE : null,
    candidates: review ? [] : [LINE],
  };
}

const matched = (by: string, approvedBy: string | null = null) => ({
  outcome: "matched",
  reason: null,
  statement_line_id: LINE.id,
  reviewed_by: by,
  reviewed_at: "2026-11-13T08:00:00Z",
  approved_by: approvedBy,
  approved_at: approvedBy ? "2026-11-13T09:00:00Z" : null,
});

const body = (init: RequestInit | undefined) => JSON.parse(String(init?.body)) as Record<string, unknown>;
const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string>)[name];

function api(fetch: typeof globalThis.fetch) {
  // No dialog is mounted, so a refusal reaches the screen as is.
  return createHttpBankTransfersApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)), ME);
}

describe("bank transfers over HTTP", () => {
  it("reads every page of the queue, and says it is waiting for a check while the list cannot tell which", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockReturnValueOnce(reply(200, { items: [PAYMENT], next_before: PAYMENT.id }))
      .mockReturnValueOnce(reply(200, { items: [{ ...PAYMENT, id: "-Npayment0000000000B", awaiting: "second_person" }], next_before: null }));
    const items = await api(fetch).listQueue();
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "https://api.test/staff/payments/bank-transfers",
      "https://api.test/staff/payments/bank-transfers?before=-Npayment0000000000A",
    ]);
    expect(items.map((item) => item.stage)).toEqual(["in_review", "to_approve"]);
    expect(items[0]).toMatchObject({ sentOn: "2026-11-12", bankName: "Stanbic Bank", amount: { minor: 2_500_000, currency: "UGX" } });
  });

  it("works out where a transfer stands and what this person may do", async () => {
    const cases: [ReturnType<typeof transfer>, string, string[]][] = [
      [transfer(null), "to_match", ["match", "refuse"]],
      [transfer(matched(OTHER)), "to_approve", ["approve"]],
      [transfer(matched(ME)), "to_approve", []],
      [transfer(matched(OTHER, ME)), "confirming", []],
      [transfer(matched(OTHER, ME), "confirmed"), "confirmed", []],
      [transfer({ ...matched(OTHER), outcome: "not_matched", reason: "amount_differs", statement_line_id: null }, "failed"), "refused", []],
    ];
    for (const [json, stage, allowed] of cases) {
      const found = await api(vi.fn<typeof globalThis.fetch>(() => reply(200, json))).getTransfer(PAYMENT.id);
      expect([found.stage, found.allowed]).toEqual([stage, allowed]);
    }
    const mine = await api(vi.fn<typeof globalThis.fetch>(() => reply(200, transfer(matched(ME))))).getTransfer(PAYMENT.id);
    expect(mine.review).toMatchObject({ reviewedByYou: true, approvedByYou: null });
    expect(mine.receipt).toEqual({ contentType: "application/pdf", byteSize: 120_000, url: "https://files.test/receipt" });
    expect(mine.matchedLine?.reference).toBe("FT2631600123");
  });

  it("sends a match, a refusal and an approval with the idempotency key", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, transfer(matched(OTHER))));
    const transfers = api(fetch);
    await transfers.match("key-1", PAYMENT.id, LINE.id);
    await transfers.refuse("key-2", PAYMENT.id, "not_on_statement");
    await transfers.approve("key-3", PAYMENT.id);
    expect(fetch.mock.calls.map((call) => [call[0], call[1]?.method])).toEqual([
      ["https://api.test/staff/payments/bank-transfers/-Npayment0000000000A/review", "POST"],
      ["https://api.test/staff/payments/bank-transfers/-Npayment0000000000A/review", "POST"],
      ["https://api.test/staff/payments/bank-transfers/-Npayment0000000000A/approve", "POST"],
    ]);
    expect(body(fetch.mock.calls[0]?.[1])).toEqual({ outcome: "matched", statement_line_id: LINE.id });
    expect(body(fetch.mock.calls[1]?.[1])).toEqual({ outcome: "not_matched", reason: "not_on_statement" });
    expect(fetch.mock.calls.map((call) => header(call[1], "Idempotency-Key"))).toEqual(["key-1", "key-2", "key-3"]);
  });

  it("words the backend's refusals", async () => {
    const cases: [() => Promise<Response>, "match" | "approve", string][] = [
      [() => refusal(422, "validation_failed", "line_already_used"), "match", "already confirms another payment"],
      [() => refusal(422, "validation_failed", "line_not_found"), "match", "no longer fits this transfer"],
      [() => refusal(409, "conflict"), "match", "Someone else has already acted"],
      [() => refusal(404, "not_found"), "match", "no longer exists"],
      [() => refusal(403, "forbidden"), "approve", "You matched it yourself."],
      [() => refusal(403, "forbidden"), "match", "not allowed to decide"],
      [() => refusal(403, "confirm_it_is_you"), "approve", "you did not confirm it is you"],
      [() => Promise.reject(new TypeError("offline")), "approve", "could not reach Finvest"],
    ];
    for (const [response, action, words] of cases) {
      const transfers = api(vi.fn<typeof globalThis.fetch>(response));
      const attempt = action === "match" ? transfers.match("key", PAYMENT.id, LINE.id) : transfers.approve("key", PAYMENT.id);
      const error = await attempt.catch((caught: unknown) => caught);
      expect(bankTransferErrorMessage(error)).toContain(words);
    }
  });
});
