import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { ComplianceError } from "./api.ts";
import { createHttpComplianceApi } from "./http.ts";

const PROPOSAL = {
  id: "-Nproposal0000000001",
  outcome: "no_concern",
  reason: "School fees, invoice seen",
  proposed_by: "-Nstaff000000000000B",
  proposed_by_name: "Grace Auma",
  proposed_at: "2026-11-13T07:40:00Z",
  status: "proposed",
  decided_by: null,
  decided_by_name: null,
  decided_at: null,
  decision_note: null,
};

const CASE = {
  id: "-Ncase00000000000001",
  client: { id: "-Nclient000000000001", name: "Ruth Achieng", account_number: "FV 2035 0118" },
  source: "monitoring",
  code: "large_deposit",
  opened_at: "2026-11-13T07:00:00Z",
  status: "open",
  outcome: null,
  closed_at: null,
  waiting: PROPOSAL,
};

const DETAIL = {
  case: CASE,
  run: null,
  matches: [],
  detail: { deposit_id: "-Ndeposit00000000001", amount_minor: 25000000, threshold: 20000000 },
  proposals: [PROPOSAL],
  standing: null,
  outcomes: ["no_concern", "report_to_fia"],
  can_propose: false,
  can_decide: true,
  yours: false,
};

function respond(status: number, body: unknown) {
  return vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const refused = (code: string, fields: unknown[] = []) => ({ request_id: "r", error: { code, message: code, fields } });
const field = (name: string, code: string) => ({ field_path: [name], code, message: code });

describe("compliance over the API", () => {
  it("reads the rules and downloads the register", async () => {
    const fetchImpl = respond(200, {
      currency: "UGX",
      large_deposit_minor: 20_000_000,
      split_deposits_days: 7,
      many_deposits_count: 5,
      many_deposits_hours: 24,
      unusual_multiple: 5,
      unusual_floor_minor: 5_000_000,
      unusual_history: 3,
      unusual_lookback_days: 180,
      quick_withdrawal_days: 7,
      quick_withdrawal_share_numerator: 1,
      quick_withdrawal_share_denominator: 2,
      quick_withdrawal_floor_minor: 5_000_000,
      rescreen_every_days: 30,
    });
    const api = createHttpComplianceApi(createHttp("https://api.example", fetchImpl));
    const rules = await api.rules();
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("https://api.example/staff/compliance/rules");
    expect(rules).toMatchObject({ largeDepositMinor: 20_000_000, quickWithdrawalShare: [1, 2], rescreenEveryDays: 30 });
    const download = vi.fn<typeof fetch>().mockResolvedValue(new Response("Case ID\r\n", { status: 200, headers: { "Content-Type": "text/csv" } }));
    const register = await createHttpComplianceApi(createHttp("https://api.example", download)).exportRegister();
    expect(download.mock.calls[0]?.[0]).toBe("https://api.example/staff/compliance/cases.csv");
    expect(download.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(register.filename).toMatch(/^finvest-compliance-register-\d{4}-\d{2}-\d{2}\.csv$/);
  });

  it("lists cases in the console's shape, with the filter in the address", async () => {
    const fetchImpl = respond(200, [CASE]);
    const api = createHttpComplianceApi(createHttp("https://api.example", fetchImpl));
    const [first] = await api.listCases({ status: "open", source: "monitoring" });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("https://api.example/staff/compliance/cases?status=open&source=monitoring");
    expect(first).toMatchObject({
      code: "large_deposit",
      client: { name: "Ruth Achieng", accountNumber: "FV 2035 0118" },
      waiting: { proposedByName: "Grace Auma", outcome: "no_concern" },
    });
  });

  it("proposes with the outcome and reason", async () => {
    const fetchImpl = respond(201, DETAIL);
    const api = createHttpComplianceApi(createHttp("https://api.example", fetchImpl));
    const detail = await api.propose(CASE.id, "report_to_fia", "STR-1");
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(`https://api.example/staff/compliance/cases/${CASE.id}/proposals`);
    expect(JSON.parse(String(init?.body))).toEqual({ outcome: "report_to_fia", reason: "STR-1" });
    expect(detail).toMatchObject({ canDecide: true, outcomes: ["no_concern", "report_to_fia"] });
  });

  it("names what the backend refused", async () => {
    const cases: [number, unknown, boolean, string][] = [
      [409, refused("conflict", [field("case_id", "proposal_waiting")]), false, "proposal_waiting"],
      [409, refused("conflict", [field("case_id", "case_closed")]), false, "case_closed"],
      [422, refused("validation_failed", [field("outcome", "outcome_does_not_fit_case")]), false, "outcome_does_not_fit_case"],
      [403, refused("forbidden"), true, "same_person"],
      [403, refused("forbidden"), false, "forbidden"],
      [404, refused("not_found"), false, "not_found"],
    ];
    for (const [status, body, deciding, code] of cases) {
      const api = createHttpComplianceApi(createHttp("https://api.example", respond(status, body)));
      const attempt = deciding ? api.approve(PROPOSAL.id) : api.propose(CASE.id, "no_concern", "x");
      await expect(attempt).rejects.toEqual(new ComplianceError(code as ComplianceError["code"]));
    }
  });
});
