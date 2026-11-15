import { describe, expect, it } from "vitest";
import { describeAction, describeRecordType } from "./actions.ts";

describe("audit actions in words", () => {
  it("reads the codes seen on the trail in the product video", () => {
    expect(describeAction("reports.downloaded")).toBe("Report downloaded");
    expect(describeAction("investments.run_request.approved")).toBe("Request to run interest again approved");
  });

  it("reads a maker-checker step on any known thing", () => {
    expect(describeAction("dealing.order.sent_back")).toBe("Dealing order sent back");
    expect(describeAction("investments.rate.proposed")).toBe("Rate change proposed");
    expect(describeAction("bonds.change.withdrawn")).toBe("Bond change withdrawn");
    expect(describeAction("kyc.check.approved")).toBe("Identity check approved");
  });

  it("reads a step joined to its thing with an underscore", () => {
    expect(describeAction("payments.reversal_done")).toBe("Payment reversal carried out");
    expect(describeAction("payments.provider_refund_approved")).toBe("Provider refund approved");
    expect(describeAction("payouts.limits_proposed")).toBe("Withdrawal limits change proposed");
    expect(describeAction("dealing.buffer_capital.in_proposed")).toBe("Paying in Finvest's own money proposed");
  });

  it("reads a module's own action as the module and what happened", () => {
    expect(describeAction("complaints.closed")).toBe("Complaint closed");
    expect(describeAction("closure.requested")).toBe("Account closure asked for");
  });

  it("still reads a code nobody has named, without dots or underscores", () => {
    expect(describeAction("settings.bank_cutoff_proposed")).toBe("Setting, bank cutoff proposed");
    expect(describeAction("brand_new.thing_happened")).toBe("Brand new thing happened");
    expect(describeAction("plain")).toBe("Plain");
    for (const code of ["a.b.c", "x.y_z", "kyc.something_new"]) expect(describeAction(code)).not.toMatch(/[._]/);
  });

  it("reads a record type in words", () => {
    expect(describeRecordType("rate_change")).toBe("Rate change");
    expect(describeRecordType("kyc_check")).toBe("Identity check");
  });
});
