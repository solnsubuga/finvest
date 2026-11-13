import { failureCopy, formatDateTime, formatMoney, formatTime } from "../format";

describe("formatMoney", () => {
  it("groups whole shillings in threes", () => {
    expect(formatMoney({ currency: "UGX", minor: 1_000_000 })).toBe("UGX 1,000,000");
    expect(formatMoney({ currency: "UGX", minor: 50_000 })).toBe("UGX 50,000");
    expect(formatMoney({ currency: "UGX", minor: 999 })).toBe("UGX 999");
    expect(formatMoney({ currency: "UGX", minor: 0 })).toBe("UGX 0");
    expect(formatMoney({ currency: "UGX", minor: 1_234_567_890 })).toBe("UGX 1,234,567,890");
  });

  it("places the point for dollars, which are counted in cents", () => {
    expect(formatMoney({ currency: "USD", minor: 50_000 })).toBe("USD 500.00");
    expect(formatMoney({ currency: "USD", minor: 123_456_789 })).toBe("USD 1,234,567.89");
    expect(formatMoney({ currency: "USD", minor: 105 })).toBe("USD 1.05");
    expect(formatMoney({ currency: "USD", minor: 7 })).toBe("USD 0.07");
    expect(formatMoney({ currency: "USD", minor: 0 })).toBe("USD 0.00");
    expect(formatMoney({ currency: "USD", minor: -250 })).toBe("USD -2.50");
    expect(formatMoney({ currency: "UGX", minor: -1_500 })).toBe("UGX -1,500");
  });

  it("refuses an amount that is not a whole number", () => {
    expect(() => formatMoney({ currency: "UGX", minor: 1000.5 })).toThrow();
    expect(() => formatMoney({ currency: "UGX", minor: Number.NaN })).toThrow();
    expect(() => formatMoney({ currency: "USD", minor: 0.5 })).toThrow();
  });
});

describe("times", () => {
  it("are shown in Kampala time, three hours ahead of UTC", () => {
    expect(formatTime("2026-11-13T12:31:00Z")).toBe("15:31");
    expect(formatDateTime("2026-11-13T12:32:10Z")).toBe("13 Nov 2026, 15:32");
  });

  it("move to the next day in Kampala late in the UTC evening", () => {
    expect(formatDateTime("2026-12-31T21:05:00Z")).toBe("1 Jan 2027, 00:05");
  });
});

describe("failure copy", () => {
  it("names the network and the number", () => {
    expect(failureCopy.insufficient_funds.sentence("MTN", "+256 772 123 456")).toBe(
      "MTN says there was not enough money on +256 772 123 456.",
    );
  });
});
