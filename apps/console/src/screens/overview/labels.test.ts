import { describe, expect, it } from "vitest";
import type { ApprovalItem } from "./api.ts";
import { describeSubject, kindLabel, linkFor } from "./labels.ts";

const item = (over: Partial<ApprovalItem>): ApprovalItem => ({
  kind: null,
  id: "-Nitem0000000000001",
  subject: "",
  proposedBy: "-Nstaff000000000000B",
  proposedByName: "Grace Auma",
  proposedAt: "2026-11-14T09:00:00Z",
  yours: false,
  canDecide: true,
  ...over,
});

describe("overview: what each waiting item is called", () => {
  it("names a booking for a client and opens Investments", () => {
    const booking = item({ kind: "investment_booking", subject: "TREA-91" });
    expect(kindLabel(booking)).toBe("Investment booked for a client");
    expect(linkFor(booking)).toBe("/investments");
    expect(describeSubject(booking)).toBe("TREA-91");
  });

  it("shows a kind it does not know without a link", () => {
    const unknown = item({ subject: "x" });
    expect(kindLabel(unknown)).toBe("Approval");
    expect(linkFor(unknown)).toBeNull();
  });
});
