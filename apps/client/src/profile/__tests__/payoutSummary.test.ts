import { payoutSummary } from "@/profile/WideProfile";
import { PayoutAccount } from "@/payouts/types";

function account(overrides: Partial<PayoutAccount>): PayoutAccount {
  return {
    id: "a",
    kind: "mobile_money",
    network: "mtn",
    bankCode: null,
    bankName: null,
    account: "+256 77• ••• 456",
    holderName: "Amara Nakato",
    status: "approved",
    proofKind: null,
    rejectionReason: null,
    nameCheck: null,
    payableFrom: null,
    firstPaidAt: null,
    createdAt: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

describe("payoutSummary", () => {
  it("is empty with no approved account", () => {
    expect(payoutSummary([])).toBeNull();
    expect(payoutSummary([account({ status: "in_review" })])).toBeNull();
  });

  it("names each network once, then counts banks", () => {
    expect(
      payoutSummary([
        account({ id: "1" }),
        account({ id: "2" }),
        account({ id: "3", kind: "bank", network: null, bankName: "Stanbic" }),
      ]),
    ).toBe("MTN and 1 bank");
    expect(
      payoutSummary([
        account({ id: "1", kind: "bank", network: null }),
        account({ id: "2", kind: "bank", network: null }),
      ]),
    ).toBe("2 banks");
  });
});
