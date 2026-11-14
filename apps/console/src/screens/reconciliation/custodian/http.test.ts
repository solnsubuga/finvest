import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { CustodianError, custodianErrorMessage } from "./api.ts";
import { createHttpCustodianChecksApi } from "./http.ts";

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const CHECK = {
  id: "-NcheckA000000000001",
  statement_date: "2026-11-13",
  status: "to_review",
  file_content_type: "application/pdf",
  file_bytes: 1200,
  uploaded_by: "-Nstaff000000000000A",
  uploaded_at: "2026-11-14T08:00:00Z",
  reader: "claude:sample",
  read_statement_date: "2026-11-13",
  custodian_name: "A custodian",
  account_reference: "FIN-1",
  amount_scale: "thousand",
  failure_code: null,
  submitted_by: null,
  submit_note: null,
  signed_off_by: null,
  signed_off_at: null,
  sign_off_note: null,
  sent_back_by: null,
  sent_back_note: null,
  allowed: ["correct", "note", "submit"],
};

const DETAIL = {
  check: CHECK,
  rows: [
    {
      id: "-NrowA00000000000001",
      line_no: 1,
      instrument: "T-Bill 91D",
      code: "UG1",
      face_value_text: "1,250",
      currency_text: "UGX",
      matures_on_text: "03/12/2026",
      location: "page 1",
      source_text: "UG1 T-Bill 91D 03/12/2026 1,250",
      currency: "UGX",
      face_minor: 1_250_000,
      matures_on: "2026-12-03",
      problem: null,
      corrected_face_minor: null,
      corrected_matures_on: null,
      left_out: false,
      corrected_by: null,
      correction_reason: null,
    },
  ],
  items: [
    {
      key: "maturity:UGX:2026-12-03",
      kind: "amount_differs",
      currency: "UGX",
      matures_on: "2026-12-03",
      finvest_face_minor: 1_000_000,
      custodian_face_minor: 1_250_000,
      difference_minor: 250_000,
      product_codes: ["TREA-91"],
      row_ids: ["-NrowA00000000000001"],
      problem: null,
      needs_note: true,
      note: null,
    },
  ],
  unexplained: 1,
};

const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string> | undefined)?.[name];
const api = (fetch: typeof globalThis.fetch) => createHttpCustodianChecksApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));

describe("custodian checks over HTTP", () => {
  it("reads the list and one check", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) => (String(url).endsWith("custodian-checks") ? reply(200, { reading_on: true, checks: [CHECK] }) : reply(200, DETAIL)));
    const checks = api(fetch);
    const list = await checks.list();
    expect(list.readingOn).toBe(true);
    expect(list.checks[0]).toMatchObject({ statementDate: "2026-11-13", amountScale: "thousand", allowed: ["correct", "note", "submit"] });
    const detail = await checks.get(CHECK.id);
    expect(detail.rows[0]).toMatchObject({ lineNo: 1, faceValueText: "1,250", faceMinor: 1_250_000, maturesOn: "2026-12-03" });
    expect(detail.items[0]).toMatchObject({ differenceMinor: 250_000, productCodes: ["TREA-91"], needsNote: true });
    expect(fetch.mock.calls[1]?.[0]).toBe(`https://api.test/staff/reconciliation/custodian-checks/${encodeURIComponent(CHECK.id)}`);
  });

  it("uploads as a form, and sends decisions with an idempotency key", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((_url, init) => reply(200, init?.body instanceof FormData ? CHECK : DETAIL));
    const checks = api(fetch);
    const file = new File(["x"], "statement.pdf", { type: "application/pdf" });
    await checks.upload("key-1", "2026-11-13", file);
    const form = fetch.mock.calls[0]?.[1]?.body as FormData;
    expect(form.get("statement_date")).toBe("2026-11-13");
    expect((form.get("file") as File).name).toBe("statement.pdf");
    expect(header(fetch.mock.calls[0]?.[1], "Idempotency-Key")).toBe("key-1");

    await checks.correctRow(CHECK.id, "-NrowA00000000000001", { leftOut: true, faceMinor: 5, maturesOn: "2026-12-03", reason: "A total" });
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ left_out: true, face_minor: null, matures_on: null, reason: "A total" });
    await checks.submit("key-2", CHECK.id, null);
    await checks.signOff("key-3", CHECK.id, "Agreed");
    await checks.sendBack("key-4", CHECK.id, "Line 2 again");
    expect(fetch.mock.calls.slice(2).map((call) => [String(call[0]).split("/").pop(), header(call[1], "Idempotency-Key")])).toEqual([
      ["submit", "key-2"],
      ["sign-off", "key-3"],
      ["send-back", "key-4"],
    ]);
  });

  it("words the backend's named refusals", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(409, { error: { code: "conflict", message: "x", fields: [{ field: "check", code: "check_needs_second_person" }] } }),
    );
    const error = await api(fetch).signOff("key-1", CHECK.id, null).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(CustodianError);
    expect(custodianErrorMessage(error)).toMatch(/a different person must sign it off/);
  });
});
