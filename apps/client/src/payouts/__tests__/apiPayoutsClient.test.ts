import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiPayoutsClient } from "../apiPayoutsClient";
import { forgetBanks } from "../banks";
import { resolvePayoutsClient } from "../resolvePayouts";
import { PayoutsError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const ID = "DDDDDDDDDDDDDDDDDDDD";

const destination = (overrides: Record<string, unknown> = {}) => ({
  id: ID,
  kind: "bank",
  network: null,
  bank_code: "stanbic",
  account: "•••• 4821",
  holder_name: "Amara Nakato",
  status: "awaiting_code",
  proof_kind: null,
  rejection_reason: null,
  payable_from: null,
  first_paid_at: null,
  created_at: "2026-11-13T09:00:00Z",
  ...overrides,
});

const payout = {
  id: "PPPPPPPPPPPPPPPPPPPP",
  destination_id: ID,
  kind: "mobile_money",
  amount: { minor: 512400, currency: "UGX" },
  status: "processing",
  business_date: "2026-11-13",
  created_at: "2026-11-13T09:00:00Z",
};

type Seen = { method: string; path: string; body: unknown };

function serve(status: number, body: unknown): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const raw = init?.body;
    seen.push({
      method: String(init?.method ?? "GET"),
      path: String(url).replace(API_URL, ""),
      body: raw instanceof FormData ? Object.fromEntries(raw.entries()) : raw ? JSON.parse(String(raw)) : null,
    });
    return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status });
  });
  return seen;
}

function refusal(code: string, fields: { field_path: string[]; code: string }[] = []) {
  return { request_id: "R".repeat(20), error: { code, message: "x", fields } };
}

/** Answers the bank list as the backend does and everything else with `body`. */
function serveWithBanks(body: unknown, banks: unknown = [{ code: "stanbic", name: "Stanbic Bank" }]): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const path = String(url).replace(API_URL, "");
    seen.push({ method: String(init?.method ?? "GET"), path, body: null });
    return new Response(JSON.stringify(path === "/client/banks" ? banks : body), { status: 200 });
  });
  return seen;
}

afterEach(() => {
  setFetch(null);
  forgetBanks();
});

describe("the payouts client for the real API", () => {
  it("is the API outside the demo", () => {
    expect(typeof resolvePayoutsClient(null).listAccounts).toBe("function");
  });

  it("lists accounts with the number as masked by the API", async () => {
    const seen = serveWithBanks([destination({ status: "approved", payable_from: "2026-11-14T09:00:00Z" })]);
    const [account] = await createApiPayoutsClient().listAccounts();
    expect(seen.map((call) => call.path).sort()).toEqual(["/client/banks", "/client/payout-destinations"]);
    expect(account).toEqual({
      id: ID,
      kind: "bank",
      network: null,
      bankCode: "stanbic",
      bankName: "Stanbic Bank",
      account: "•••• 4821",
      holderName: "Amara Nakato",
      status: "approved",
      proofKind: null,
      rejectionReason: null,
      nameCheck: null,
      payableFrom: "2026-11-14T09:00:00Z",
      firstPaidAt: null,
      createdAt: "2026-11-13T09:00:00Z",
    });
  });

  it("reads the bank list once and names an account by it", async () => {
    const seen = serveWithBanks(destination());
    const client = createApiPayoutsClient();
    expect(await client.listBanks()).toEqual([{ code: "stanbic", name: "Stanbic Bank" }]);
    expect((await client.getAccount(ID)).bankName).toBe("Stanbic Bank");
    expect(seen.filter((call) => call.path === "/client/banks")).toHaveLength(1);
  });

  it("still shows an account when the bank list cannot be read, and reads it again next time", async () => {
    setFetch(async (url) =>
      String(url).endsWith("/client/banks")
        ? new Response(JSON.stringify(refusal("internal")), { status: 500 })
        : new Response(JSON.stringify(destination()), { status: 200 }),
    );
    const client = createApiPayoutsClient();
    expect((await client.getAccount(ID)).bankName).toBeNull();
    await expect(client.listBanks()).rejects.toEqual(new PayoutsError("unavailable"));
    serveWithBanks(destination());
    expect((await client.getAccount(ID)).bankName).toBe("Stanbic Bank");
  });

  it("adds a mobile money number or a bank account in the API's shape", async () => {
    const client = createApiPayoutsClient();
    let seen = serve(201, destination({ kind: "mobile_money", network: "mtn", bank_code: null }));
    await client.addAccount({ kind: "mobile_money", phone: "+256772123456", holderName: " Amara Nakato " });
    expect(seen[0]).toEqual({
      method: "POST",
      path: "/client/payout-destinations",
      body: { kind: "mobile_money", phone: "+256772123456", holder_name: "Amara Nakato" },
    });
    seen = serve(201, destination());
    await client.addAccount({ kind: "bank", bankCode: "stanbic", accountNumber: "9030012344821", holderName: "Amara Nakato" });
    expect(seen[0]!.body).toEqual({
      kind: "bank",
      bank_code: "stanbic",
      account_number: "9030012344821",
      holder_name: "Amara Nakato",
    });
  });

  it("says why an account was refused", async () => {
    const client = createApiPayoutsClient();
    const add = () => client.addAccount({ kind: "mobile_money", phone: "+256712123456", holderName: "A" });
    serve(409, refusal("conflict"));
    await expect(add()).rejects.toEqual(new PayoutsError("already_saved"));
    serve(409, refusal("conflict", [{ field_path: ["body"], code: "too_many_unconfirmed_accounts" }]));
    await expect(add()).rejects.toEqual(new PayoutsError("too_many_unconfirmed"));
    serve(422, refusal("validation_failed", [{ field_path: ["body", "phone"], code: "phone_network_unsupported" }]));
    await expect(add()).rejects.toEqual(new PayoutsError("phone_network_unsupported"));
    serve(422, refusal("validation_failed", [{ field_path: ["body", "account_number"], code: "bank_account_number_invalid" }]));
    await expect(add()).rejects.toEqual(new PayoutsError("account_number_invalid"));
    serve(422, refusal("validation_failed", [{ field_path: ["body", "bank_code"], code: "bank_unknown" }]));
    await expect(add()).rejects.toEqual(new PayoutsError("bank_invalid"));
    serve(422, refusal("validation_failed", [{ field_path: ["body", "holder_name"], code: "string_too_short" }]));
    await expect(add()).rejects.toEqual(new PayoutsError("holder_name_invalid"));
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(add()).rejects.toEqual(new PayoutsError("unavailable"));
  });

  it("texts the code, checks it and says what went wrong", async () => {
    const client = createApiPayoutsClient();
    let seen = serve(204, null);
    await client.sendCode(ID);
    expect(seen[0]).toEqual({ method: "POST", path: `/client/payout-destinations/${ID}/code`, body: null });

    seen = serve(200, destination({ status: "awaiting_proof" }));
    expect((await client.verifyCode(ID, "123456")).status).toBe("awaiting_proof");
    expect(seen[0]).toEqual({ method: "POST", path: `/client/payout-destinations/${ID}/code/verify`, body: { code: "123456" } });

    serve(401, refusal("invalid_code"));
    await expect(client.verifyCode(ID, "000000")).rejects.toEqual(new PayoutsError("invalid_code"));
    serve(410, refusal("expired"));
    await expect(client.verifyCode(ID, "000000")).rejects.toEqual(new PayoutsError("code_expired"));
    serve(429, refusal("rate_limited"));
    await expect(client.sendCode(ID)).rejects.toEqual(new PayoutsError("too_many_codes"));
    serve(409, refusal("conflict", [{ field_path: ["body"], code: "destination_not_awaiting_code" }]));
    await expect(client.sendCode(ID)).rejects.toEqual(new PayoutsError("moved_on"));
  });

  it("uploads proof as a form and removes an account", async () => {
    const client = createApiPayoutsClient();
    const seen = serve(200, destination({ status: "in_review", proof_kind: "bank_letter" }));
    const file = { uri: "file:///letter.pdf", name: "letter.pdf", mimeType: "application/pdf" as const, sizeBytes: 2048 };
    expect((await client.uploadProof(ID, "bank_letter", file)).status).toBe("in_review");
    expect(seen[0]!.path).toBe(`/client/payout-destinations/${ID}/proof`);
    expect(seen[0]!.body).toMatchObject({ proof_kind: "bank_letter" });

    serve(422, refusal("validation_failed", [{ field_path: ["body", "file"], code: "unsupported_content_type" }]));
    await expect(client.uploadProof(ID, "bank_letter", file)).rejects.toEqual(new PayoutsError("file_not_accepted"));

    const removed = serve(204, null);
    await client.removeAccount(ID);
    expect(removed[0]).toEqual({ method: "DELETE", path: `/client/payout-destinations/${ID}`, body: null });
  });

  it("reads payouts and today's limits with every amount as sent", async () => {
    const client = createApiPayoutsClient();
    serve(200, [payout]);
    expect(await client.listPayouts()).toEqual([
      {
        id: payout.id,
        destinationId: ID,
        kind: "mobile_money",
        amount: { currency: "UGX", minor: 512400 },
        status: "processing",
        businessDate: "2026-11-13",
        createdAt: "2026-11-13T09:00:00Z",
        returnedAt: null,
        resentAs: null,
      },
    ]);
    serve(200, {
      effective_from: "2026-11-01",
      mobile_money_per_payout: { minor: 5000000, currency: "UGX" },
      mobile_money_per_day: null,
      bank_per_payout: null,
      bank_per_day: { minor: 50000000, currency: "UGX" },
    });
    expect(await client.getLimits()).toEqual({
      effectiveFrom: "2026-11-01",
      mobileMoneyPerPayout: { currency: "UGX", minor: 5000000 },
      mobileMoneyPerDay: null,
      bankPerPayout: null,
      bankPerDay: { currency: "UGX", minor: 50000000 },
    });
  });

  it("sends a payout that came back again, and names each refusal", async () => {
    const client = createApiPayoutsClient();
    serve(200, { ...payout, status: "paid", returned_at: "2026-11-12T10:00:00Z" });
    expect(await client.getPayout(payout.id)).toMatchObject({ status: "paid", returnedAt: "2026-11-12T10:00:00Z", resentAs: null });
    serve(200, { ...payout, status: "paid", returned_at: "2026-11-12T10:00:00Z", resent_as: "NNNNNNNNNNNNNNNNNNNN" });
    expect(await client.getPayout(payout.id)).toMatchObject({ resentAs: "NNNNNNNNNNNNNNNNNNNN" });

    const seen = serve(201, { ...payout, id: "NNNNNNNNNNNNNNNNNNNN" });
    expect(await client.resend(payout.id, ID)).toMatchObject({ id: "NNNNNNNNNNNNNNNNNNNN", returnedAt: null });
    expect(seen[0]).toEqual({ method: "POST", path: `/client/payouts/${payout.id}/resend`, body: { destination_id: ID } });

    const resend = () => client.resend(payout.id, ID);
    serve(409, refusal("conflict", [{ field_path: ["body"], code: "payout_not_returned" }]));
    await expect(resend()).rejects.toEqual(new PayoutsError("not_returned"));
    serve(422, refusal("idempotency_key_reused"));
    await expect(resend()).rejects.toEqual(new PayoutsError("already_resent"));
    for (const code of ["destination_not_approved", "destination_in_safety_wait", "destination_not_known_yet"]) {
      serve(422, refusal("validation_failed", [{ field_path: ["body", "destination_id"], code }]));
      await expect(resend()).rejects.toEqual(new PayoutsError("account_not_ready"));
    }
    serve(422, refusal("validation_failed", [{ field_path: ["body", "destination_id"], code: "over_mobile_money_limit" }]));
    await expect(resend()).rejects.toEqual(new PayoutsError("over_mobile_money_limit"));
    serve(422, refusal("validation_failed", [{ field_path: ["body", "destination_id"], code: "over_daily_limit" }]));
    await expect(resend()).rejects.toEqual(new PayoutsError("over_limit"));
    serve(422, refusal("validation_failed", [{ field_path: ["body", "destination_id"], code: "insufficient_funds" }]));
    await expect(resend()).rejects.toEqual(new PayoutsError("invalid"));
    serve(503, refusal("unavailable"));
    await expect(resend()).rejects.toEqual(new PayoutsError("unavailable"));
  });

  it("asks the network for a number's name again, and names each refusal", async () => {
    const seen = serve(200, destination({ kind: "mobile_money", network: "airtel", bank_code: null, status: "awaiting_proof", name_check: "checking" }));
    const account = await createApiPayoutsClient().checkNameAgain(ID);
    expect(account).toMatchObject({ status: "awaiting_proof", nameCheck: "checking" });
    expect(seen.map((call) => `${call.method} ${call.path}`)).toContain(`POST /client/payout-destinations/${ID}/name-check`);
    const refusals: [string, PayoutsError["code"]][] = [
      ["name_check_in_progress", "name_check_in_progress"],
      ["name_check_limit_reached", "name_check_limit_reached"],
      ["client_not_verified", "id_not_approved"],
      ["destination_not_awaiting_proof", "moved_on"],
    ];
    for (const [reason, expected] of refusals) {
      serve(409, refusal("conflict", [{ field_path: ["body"], code: reason }]));
      await expect(createApiPayoutsClient().checkNameAgain(ID)).rejects.toEqual(new PayoutsError(expected));
    }
  });

  it("has no name check for an account the API sends without one", async () => {
    serve(200, [destination()]);
    const [account] = await createApiPayoutsClient().listAccounts();
    expect(account?.nameCheck).toBeNull();
  });
});
