import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { reviewQueueErrorMessage } from "./api.ts";
import { createHttpReviewQueueApi } from "./http.ts";

const ACCOUNT = {
  id: "-NdestA0000000000001",
  client_id: "-Nclient00000000000A",
  kind: "bank",
  network: null,
  bank_code: "stanbic",
  account: "•••• 4821",
  holder_name: "Peter Okello",
  status: "in_review",
  proof_kind: "bank_statement",
  check: {
    proof_name: "Peter Okello",
    name_match: "partial",
    statement_date: "2026-11-01",
    checked_by: "-Nstaff000000000000A",
    checked_at: "2026-11-13T08:00:00Z",
  },
  rejection_reason: null,
  approved_by: null,
  approved_at: null,
  payable_from: null,
  created_at: "2026-11-12T08:00:00Z",
};

const DETAIL = {
  ...ACCOUNT,
  account_number: "9030012744821",
  id_name: "OKELLO PETER JAMES",
  proof_link: { url: "https://files.test/proof?sig=1", expires_at: "2026-11-13T08:05:00Z" },
};

const HELD = {
  id: "-NpayoutA00000000001",
  client_id: "-Nclient00000000000A",
  destination_id: "-NdestA0000000000001",
  kind: "mobile_money",
  amount: { minor: 12_500_000, currency: "UGX" },
  business_date: "2026-11-13",
  created_at: "2026-11-13T09:00:00Z",
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string, field?: string) {
  const fields = field ? [{ field_path: ["body"], code: field }] : [];
  return reply(status, { request_id: "req", error: { code, message: code, fields } });
}

function apiWith(fetch: typeof globalThis.fetch, confirm = () => Promise.resolve(false)) {
  return createHttpReviewQueueApi(createHttp("https://api.test", fetch, confirm));
}

function sent(fetch: ReturnType<typeof vi.fn<typeof globalThis.fetch>>, call = 0) {
  const [url, init] = fetch.mock.calls[call]!;
  return { url, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : null, headers: init?.headers as Record<string, string> };
}

describe("review queue over HTTP", () => {
  it("lists accounts with the masked number and the first person's check", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [ACCOUNT]));
    const [item] = await apiWith(fetch).listAccounts();
    expect(sent(fetch).url).toBe("https://api.test/staff/payout-destinations");
    expect(item).toMatchObject({
      id: ACCOUNT.id,
      clientId: ACCOUNT.client_id,
      bankCode: "stanbic",
      account: "•••• 4821",
      holderName: "Peter Okello",
      proofKind: "bank_statement",
      check: { proofName: "Peter Okello", nameMatch: "partial", statementDate: "2026-11-01", checkedBy: "-Nstaff000000000000A" },
    });
  });

  it("opens one account with its whole number, the ID name and the proof link", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, DETAIL));
    const detail = await apiWith(fetch).openAccount(ACCOUNT.id);
    expect(sent(fetch)).toMatchObject({ url: `https://api.test/staff/payout-destinations/${ACCOUNT.id}`, method: "GET" });
    expect(detail).toMatchObject({
      accountNumber: "9030012744821",
      idName: "OKELLO PETER JAMES",
      proofLink: { url: "https://files.test/proof?sig=1", expiresAt: "2026-11-13T08:05:00Z" },
    });
  });

  it("sends a check, with the statement date only when there is one", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, ACCOUNT));
    const api = apiWith(fetch);
    await api.check(ACCOUNT.id, { proofName: "Peter Okello", statementDate: "2026-11-01", reason: "Statement matches" });
    await api.check(ACCOUNT.id, { proofName: "Peter Okello", statementDate: null, reason: "Card matches" });
    expect(sent(fetch, 0)).toMatchObject({
      url: `https://api.test/staff/payout-destinations/${ACCOUNT.id}/check`,
      method: "POST",
      body: { proof_name: "Peter Okello", statement_date: "2026-11-01", reason: "Statement matches" },
    });
    expect(sent(fetch, 1).body).toEqual({ proof_name: "Peter Okello", reason: "Card matches" });
  });

  it("sends approve, reject and remove to their routes", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, ACCOUNT));
    const api = apiWith(fetch);
    await api.approve(ACCOUNT.id, "Same person");
    await api.reject(ACCOUNT.id, "proof_too_old", "From July");
    await api.remove(ACCOUNT.id, "Client asked");
    expect([0, 1, 2].map((call) => [sent(fetch, call).url, sent(fetch, call).body])).toEqual([
      [`https://api.test/staff/payout-destinations/${ACCOUNT.id}/approve`, { reason: "Same person" }],
      [`https://api.test/staff/payout-destinations/${ACCOUNT.id}/reject`, { rejection_reason: "proof_too_old", reason: "From July" }],
      [`https://api.test/staff/payout-destinations/${ACCOUNT.id}/remove`, { reason: "Client asked" }],
    ]);
  });

  it("lists held payouts and decides one with the screen's idempotency key", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).endsWith("/held") ? reply(200, [HELD]) : reply(200, { id: HELD.id, decision: "approved", status: "queued" }),
    );
    const api = apiWith(fetch);
    const [payout] = await api.listHeld();
    expect(payout).toEqual({
      id: HELD.id,
      clientId: HELD.client_id,
      destinationId: HELD.destination_id,
      kind: "mobile_money",
      amount: { minor: 12_500_000, currency: "UGX" },
      businessDate: "2026-11-13",
      createdAt: "2026-11-13T09:00:00Z",
    });
    expect(await api.decideHeld(HELD.id, "approve", "Known client", "key-1")).toEqual({ id: HELD.id, decision: "approved", status: "queued" });
    await api.decideHeld(HELD.id, "decline", "Asked by phone", "key-2");
    expect(sent(fetch, 1)).toMatchObject({ url: `https://api.test/staff/payouts/${HELD.id}/approve`, body: { reason: "Known client" } });
    expect(sent(fetch, 1).headers["Idempotency-Key"]).toBe("key-1");
    expect(sent(fetch, 2)).toMatchObject({ url: `https://api.test/staff/payouts/${HELD.id}/decline`, body: { reason: "Asked by phone" } });
    expect(sent(fetch, 2).headers["Idempotency-Key"]).toBe("key-2");
  });

  it("sends a decision again with the same key once the person confirms it is them", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => refusal(403, "confirm_it_is_you"))
      .mockImplementationOnce(() => reply(200, { id: HELD.id, decision: "approved", status: "queued" }));
    await apiWith(fetch, () => Promise.resolve(true)).decideHeld(HELD.id, "approve", "Known client", "key-1");
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(sent(fetch, 1).headers["Idempotency-Key"]).toBe("key-1");
  });

  it.each([
    [refusal(422, "validation_failed", "name_different"), "name_different"],
    [refusal(422, "validation_failed", "statement_date_required"), "statement_date_required"],
    [refusal(422, "validation_failed", "statement_too_old"), "statement_too_old"],
    [refusal(422, "validation_failed", "too_long"), "invalid"],
    [refusal(409, "conflict", "client_not_verified"), "client_not_verified"],
    [refusal(409, "conflict", "destination_not_checked"), "destination_not_checked"],
    [refusal(409, "conflict", "account_in_use"), "account_in_use"],
    [refusal(409, "conflict", "payout_not_held"), "payout_not_held"],
    [refusal(409, "conflict", "something_new"), "conflict"],
    [refusal(403, "forbidden", "checked_by_you"), "checked_by_you"],
    [refusal(403, "forbidden"), "forbidden"],
    [refusal(403, "confirm_it_is_you"), "not_confirmed"],
    [refusal(422, "idempotency_key_reused"), "key_reused"],
    [refusal(404, "not_found"), "not_found"],
    [refusal(500, "internal_error"), "unavailable"],
  ])("turns a refusal into a code the screen words (%#)", async (answer, code) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => answer);
    await expect(apiWith(fetch).approve(ACCOUNT.id, "Same person")).rejects.toMatchObject({ code });
  });

  it("says Finvest could not be reached when the network fails", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => Promise.reject(new TypeError("offline")));
    const failure = await apiWith(fetch).listHeld().catch((error: unknown) => error);
    expect(reviewQueueErrorMessage(failure)).toBe("We could not reach Finvest. Check your connection and try again.");
  });
});
