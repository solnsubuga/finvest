import { checkName, checkRegistration, problemsOf } from "../rules";
import { approvalChoices, Organisation } from "../types";

describe("an organisation's name", () => {
  it.each([
    ["  Kampala   Traders Ltd ", "Kampala Traders Ltd"],
    ["Kira Rd. Savings Group", "Kira Rd. Savings Group"],
    ["Women’s Savings Club", "Women's Savings Club"],
    ["Café Société 2", "Café Société 2"],
  ])("keeps %p as %p", (given, kept) => {
    expect(checkName(given)).toEqual({ name: kept });
  });

  it.each(["Win at http://x.example", "finvest-help.com", "me@example", "call 0700 123456", "-Traders", "Traders!"])(
    "refuses %p",
    (given) => {
      expect(checkName(given)).toEqual({ problem: "not_a_name" });
    },
  );

  it("needs one", () => {
    expect(checkName("   ")).toEqual({ problem: "required" });
    expect(checkName("x".repeat(121))).toEqual({ problem: "required" });
  });
});

describe("a registration number", () => {
  it("is tidied, optional, and must look like one", () => {
    expect(checkRegistration(" ug/2024/123 ")).toBe("UG/2024/123");
    expect(checkRegistration("")).toBeNull();
    expect(checkRegistration("#12")).toBe("invalid");
  });
});

describe("what is left before handing in", () => {
  it("lists every problem in order", () => {
    expect(
      problemsOf({ kind: "company", registrationNumber: null, approvalsNeeded: 3, joined: 0, invited: 2, documents: ["board_resolution"] }),
    ).toEqual([
      "signatories_waiting",
      "too_few_signatories",
      "approvals_more_than_signatories",
      "documents_missing",
      "registration_number_missing",
    ]);
  });

  it("needs two people and two approvals for a group", () => {
    expect(
      problemsOf({ kind: "group", registrationNumber: null, approvalsNeeded: 1, joined: 1, invited: 0, documents: ["constitution", "group_resolution"] }),
    ).toEqual(["too_few_signatories", "approvals_too_few"]);
  });
});

function organisation(kind: Organisation["kind"], people: number): Organisation {
  return {
    kind,
    signatories: Array.from({ length: people }, (_, i) => ({ status: i ? "invited" : "joined" })),
  } as unknown as Organisation;
}

describe("the rule for withdrawals", () => {
  it("offers a sole director for a company of one", () => {
    expect(approvalChoices(organisation("company", 1)).map((c) => c.label)).toEqual(["The one signatory"]);
  });

  it("offers any number of three for a company", () => {
    expect(approvalChoices(organisation("company", 3)).map((c) => c.label)).toEqual([
      "Any 1 of 3 signatories",
      "Any 2 of 3 signatories",
      "All 3 signatories",
    ]);
  });

  it("starts at two for a group, even before the second person is asked", () => {
    expect(approvalChoices(organisation("group", 1)).map((c) => c.label)).toEqual(["All 2 signatories"]);
    expect(approvalChoices(organisation("group", 3)).map((c) => c.label)).toEqual(["Any 2 of 3 signatories", "All 3 signatories"]);
  });
});
