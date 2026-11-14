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
