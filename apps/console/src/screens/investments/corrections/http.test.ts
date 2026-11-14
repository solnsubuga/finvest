import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { CorrectionsError } from "./api.ts";
import { createHttpCorrectionsApi } from "./http.ts";

const CORRECTION = {
  id: "-Ncorrection00000001",
  kind: "refund",
  investment_id: "-Ninvestment00000001",
  client_id: "-Nclient000000000001",
  amount_minor: 500000,
  currency: "UGX",
  destination_id: "-Ndestination0000001",
  reason: "Paid after the window closed",
  status: "proposed",
  proposed_by: "-Nstaff000000000000B",
  proposed_at: "2026-11-13T07:40:00Z",
  decided_by: null,
  decided_at: null,
  decision_note: null,
  done_at: null,
  entry_id: null,
  payout_id: null,
  failure_code: null,
};

function respond(status: number, body: unknown) {
  return vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const refused = (code: string, fields: unknown[] = []) => ({ request_id: "r", error: { code, message: code, fields } });

describe("investments: corrections over the API", () => {
  it("lists them in the console's shape", async () => {
    const fetchImpl = respond(200, [CORRECTION]);
    const api = createHttpCorrectionsApi(createHttp("https://api.example", fetchImpl));
    const [first] = await api.list();
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("https://api.example/staff/investment-corrections?limit=200");
    expect(first).toMatchObject({
      kind: "refund",
      investmentId: "-Ninvestment00000001",
      amount: { minor: 500000, currency: "UGX" },
      destinationId: "-Ndestination0000001",
      proposedBy: "-Nstaff000000000000B",
    });
  });

  it("proposes with the key, and never sends a payout account for an unwind", async () => {
    const fetchImpl = respond(201, { ...CORRECTION, kind: "unwind", destination_id: null });
    const api = createHttpCorrectionsApi(createHttp("https://api.example", fetchImpl));
    await api.propose("key-1", { kind: "unwind", investmentId: "-Ninvestment00000001", reason: "Wrong client", destinationId: "ignored" });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/staff/investment-corrections");
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-1");
    expect(JSON.parse(String(init?.body))).toEqual({ kind: "unwind", investment_id: "-Ninvestment00000001", reason: "Wrong client" });
  });

  it("sends the payout account for a pay out of held maturity money", async () => {
    const fetchImpl = respond(201, { ...CORRECTION, kind: "pay_out" });
    const api = createHttpCorrectionsApi(createHttp("https://api.example", fetchImpl));
    await api.propose("key-3", { kind: "pay_out", investmentId: "-Ninvestment00000001", reason: "Client called", destinationId: "-Ndestination0000001" });
    const [, init] = fetchImpl.mock.calls[0] ?? [];
    expect(JSON.parse(String(init?.body))).toEqual({
      kind: "pay_out",
      investment_id: "-Ninvestment00000001",
      reason: "Client called",
      destination_id: "-Ndestination0000001",
    });
  });

  it("sends a decision to its own route", async () => {
    const fetchImpl = respond(200, { ...CORRECTION, status: "sent_back" });
    const api = createHttpCorrectionsApi(createHttp("https://api.example", fetchImpl));
    await api.sendBack("key-2", "-Ncorrection00000001", "Check the statement");
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/staff/investment-corrections/-Ncorrection00000001/send-back");
    expect(JSON.parse(String(init?.body))).toEqual({ note: "Check the statement" });
  });

  it("maps refusals to what the screen says", async () => {
    const cases: [number, unknown, string, "approve" | "propose"][] = [
      [403, refused("forbidden"), "same_person", "approve"],
      [403, refused("forbidden"), "forbidden", "propose"],
      [403, refused("confirm_it_is_you"), "not_confirmed", "approve"],
      [409, refused("conflict"), "conflict", "approve"],
      [404, refused("not_found"), "not_found", "approve"],
    ];
    for (const [status, body, code, step] of cases) {
      // No dialog is mounted, so a confirm_it_is_you refusal reaches the screen as it is.
      const api = createHttpCorrectionsApi(createHttp("https://api.example", respond(status, body), () => Promise.resolve(false)));
      const call =
        step === "approve"
          ? api.approve("k", "-Ncorrection00000001")
          : api.propose("k", { kind: "unwind", investmentId: "-Ninvestment00000001", reason: "x", destinationId: null });
      await expect(call).rejects.toMatchObject({ code });
    }
  });

  it("keeps the field problems of a refused proposal", async () => {
    const fetchImpl = respond(
      422,
      refused("validation_failed", [
        { field_path: ["destination_id"], code: "over_mobile_money_limit" },
        { field_path: ["body", "reason"], code: "string_too_long" },
      ]),
    );
    const api = createHttpCorrectionsApi(createHttp("https://api.example", fetchImpl));
    const error = await api
      .propose("k", { kind: "refund", investmentId: "-Ninvestment00000001", reason: "x", destinationId: "-Ndestination0000001" })
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(CorrectionsError);
    expect((error as CorrectionsError).problems).toEqual([
      { field: "destination_id", code: "over_mobile_money_limit" },
      { field: "reason", code: "too_long" },
    ]);
  });
});
