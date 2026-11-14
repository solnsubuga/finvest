import { savingLine } from "../savings";
import { Saving } from "../types";

const base: Saving = {
  investmentId: "inv-1",
  productName: "91 Day Treasury Bill",
  status: "active",
  amount: { currency: "UGX", minor: 500_000 },
  value: { currency: "UGX", minor: 510_000 },
  payBy: "2026-11-14T12:00:00Z",
  startDate: "2026-11-14",
  maturityDate: "2027-02-13",
};

describe("a goal saving's line", () => {
  it("says what each saving is doing now", () => {
    expect(savingLine(base)).toMatch(/^Started .*, matures /);
    expect(savingLine({ ...base, maturityDate: null })).toBe("Running");
    expect(savingLine({ ...base, status: "withdrawn" })).toBe(
      "Taken out early. No longer counts towards this goal",
    );
    expect(savingLine({ ...base, status: "transferred" })).toMatch(/^Sent to another client/);
    expect(savingLine({ ...base, status: "matured" })).toMatch(/^Matured /);
  });
});
