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

  it("names notes to send again with their days and whose they are", () => {
    const every = item({ kind: "note_reissue", subject: "2026-11-01 2026-11-13" });
    expect(kindLabel(every)).toBe("Investment notes to send again");
    expect(linkFor(every)).toBe("/investments");
    expect(describeSubject(every)).toBe("Issued 2026-11-01 to 2026-11-13, every client");
    expect(describeSubject(item({ kind: "note_reissue", subject: "2026-11-13 2026-11-13 -Nclient00000000001" }))).toBe(
      "Issued 2026-11-13, client -Nclient00000000001",
    );
  });

  it("shows a kind it does not know without a link", () => {
    const unknown = item({ subject: "x" });
    expect(kindLabel(unknown)).toBe("Approval");
    expect(linkFor(unknown)).toBeNull();
  });
});
