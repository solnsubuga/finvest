import { act, fireEvent, screen, within } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** 16 November 2026 in Kampala: the day of the worked example in docs/bonds.md. */
const mockClock = { ms: Date.parse("2026-11-16T09:00:00Z") };
const mockBuy = jest.fn();
const mockFailBond: { value: boolean } = { value: false };
const mockBondSample: { value: "default" | "empty" } = { value: "empty" };
const mockSell = jest.fn();
const mockInvestSample: { value: "default" | "empty" } = { value: "empty" };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/payments/stubPaymentsClient", () => {
  const actual = jest.requireActual("@/payments/stubPaymentsClient");
  return {
    ...actual,
    createStubPaymentsClient: () =>
      actual.createStubPaymentsClient({ delayMs: 0, now: () => mockClock.ms, scenarioFor: () => "00" }),
  };
});

jest.mock("@/investing/stubInvestingClient", () => {
  const actual = jest.requireActual("@/investing/stubInvestingClient");
  return {
    ...actual,
    createStubInvestingClient: () => actual.createStubInvestingClient({ delayMs: 0, now: () => mockClock.ms, sample: mockInvestSample.value }),
  };
});

jest.mock("@/bonds/stubBondsClient", () => {
  const actual = jest.requireActual("@/bonds/stubBondsClient");
  return {
    ...actual,
    createStubBondsClient: () => {
      const client = actual.createStubBondsClient({ delayMs: 0, now: () => mockClock.ms, sample: mockBondSample.value });
      return {
        ...client,
        listBonds: async () => {
          if (mockFailBond.value) {
            const { BondsError } = jest.requireActual("@/bonds/types");
            throw new BondsError("unavailable");
          }
          return client.listBonds();
        },
        buy: async (request: unknown) => {
          mockBuy(request);
          return client.buy(request);
        },
        sell: async (request: unknown) => {
          mockSell(request);
          return client.sell(request);
        },
      };
    },
  };
});

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("@/payments/pollInterval", () => ({ POLL_MS: 25 }));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function signIn() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByTestId("home");
}

async function openBond(code = "BOND-10Y-2037") {
  await signIn();
  await fireEvent.press(screen.getByLabelText("Invest"));
  await fireEvent.press(await screen.findByTestId(`bond-${code}`));
  await screen.findByTestId("bond-facts");
}

async function quoteFor(face: string) {
  await fireEvent.changeText(await screen.findByTestId("face"), face);
  await screen.findByText(/Face value|smallest face value|fixed steps/, undefined, { timeout: 2000 });
}

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  mockBuy.mockClear();
  mockFailBond.value = false;
  mockBondSample.value = "empty";
  mockSell.mockClear();
  mockInvestSample.value = "empty";
  mockClock.ms = Date.parse("2026-11-16T09:00:00Z");
});

describe("bonds", () => {
  it("lists bonds under the bills with the coupon after tax and never the tax", async () => {
    await signIn();
    await fireEvent.press(screen.getByLabelText("Invest"));
    const bond = await screen.findByTestId("bond-BOND-10Y-2037");
    expect(within(bond).getByText("14.40%")).toBeTruthy();
    expect(within(bond).getByText("From UGX 100,000 · price 98.50 per 100")).toBeTruthy();
    expect(within(screen.getByTestId("bond-BOND-2Y-2028")).getByText("Paused for new buyers")).toBeTruthy();
    expect(screen.getByTestId("product-TREA-91")).toBeTruthy();
    expect(screen.queryByText(/16(\.00)?%/)).toBeNull();
  });

  it("keeps the bills when the bonds cannot be read", async () => {
    mockFailBond.value = true;
    await signIn();
    await fireEvent.press(screen.getByLabelText("Invest"));
    expect(await screen.findByTestId("bonds-retry")).toBeTruthy();
    expect(screen.getByTestId("product-TREA-91")).toBeTruthy();
    mockFailBond.value = false;
    await fireEvent.press(screen.getByTestId("bonds-retry"));
    expect(await screen.findByTestId("bond-BOND-10Y-2037")).toBeTruthy();
  });

  it("shows the bond's terms and quotes a face value from the backend's figures", async () => {
    await openBond();
    const facts = screen.getByTestId("bond-facts");
    expect(within(facts).getByLabelText("ISIN: UG12K1405378")).toBeTruthy();
    expect(within(facts).getByLabelText("Coupons: Every six months, next on 14 May 2027")).toBeTruthy();
    expect(within(facts).getByLabelText("Price today: 98.50 per 100")).toBeTruthy();
    await quoteFor("1000000");
    const quote = await screen.findByTestId("bond-quote");
    expect(within(quote).getByLabelText("Interest since the last coupon: UGX 883")).toBeTruthy();
    expect(within(quote).getByLabelText("You pay: UGX 985,883")).toBeTruthy();
    expect(within(quote).getByLabelText("Each coupon after tax: UGX 72,000")).toBeTruthy();
    expect(within(quote).getByLabelText("Back by maturity: UGX 2,512,000")).toBeTruthy();
    expect(screen.getByTestId("bond-review")).not.toBeDisabled();
  });

  it("refuses a face value that is not a whole number of steps", async () => {
    await openBond();
    await quoteFor("150000");
    expect(await screen.findByText(/go up in fixed steps/)).toBeTruthy();
    expect(screen.getByTestId("bond-review")).toBeDisabled();
  });

  it("offers no buying on a paused bond", async () => {
    await openBond("BOND-2Y-2028");
    expect(screen.getByText(/paused for new buyers/)).toBeTruthy();
    expect(screen.queryByTestId("face")).toBeNull();
    expect(screen.queryByTestId("bond-review")).toBeNull();
  });

  it("reviews, buys once, and pays the cost by mobile money, with no bank transfer offered", async () => {
    await openBond();
    await quoteFor("1000000");
    await screen.findByTestId("bond-quote");
    await fireEvent.press(screen.getByTestId("bond-review"));
    await screen.findByText("Review your bond");
    await fireEvent.press(await screen.findByTestId("bond-confirm"));
    await screen.findByText("Pay for your bond");
    expect(mockBuy).toHaveBeenCalledTimes(1);
    expect(mockBuy.mock.calls[0][0]).toMatchObject({ code: "BOND-10Y-2037", face: { currency: "UGX", minor: 1_000_000 } });
    expect(screen.getByText("Pay UGX 985,883")).toBeTruthy();
    expect(screen.queryByTestId("pay-by-bank")).toBeNull();

    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");
    await act(async () => {
      mockClock.ms += 8_000;
    });
    await screen.findByText("Completed");
    expect(screen.getByText(/coupons are paid every six months/)).toBeTruthy();
  });
});

describe("bond holdings", () => {
  async function openPortfolio() {
    mockBondSample.value = "default";
    await signIn();
    await act(async () => {
      router.push("/portfolio");
    });
    return screen.findByTestId("bond-holdings");
  }

  it("lists the client's bonds in Portfolio with what waits on them", async () => {
    const section = await openPortfolio();
    expect(within(section).getByText("2 Year Treasury Bond 2028")).toBeTruthy();
    expect(within(section).getByText("A coupon of UGX 56,000 is waiting for an account")).toBeTruthy();
    expect(within(section).getByText("5 Year Treasury Bond 2031")).toBeTruthy();
    expect(within(section).getByText("Next coupon 22 Feb 2027")).toBeTruthy();
  });

  it("says the totals leave bonds out, and treats a client with only bonds as not new", async () => {
    await openPortfolio();
    expect(screen.getByText("Your bonds are below. You have no other investments yet.")).toBeTruthy();
    await act(async () => {
      router.push("/");
    });
    expect(await screen.findByTestId("bonds-left-out")).toHaveTextContent(/leave out your bonds/);
    expect(screen.queryByText(/Nothing here yet/)).toBeNull();
  });

  it("labels the investment totals beside bonds", async () => {
    mockInvestSample.value = "default";
    await openPortfolio();
    expect(await screen.findByTestId("portfolio-totals")).toBeTruthy();
    expect(await screen.findByText(/Each bond has its own figures under Bonds below/)).toBeTruthy();
  });

  it("shows no bonds section to a client with none", async () => {
    await signIn();
    await act(async () => {
      router.push("/portfolio");
    });
    await screen.findByTestId("go-invest");
    expect(screen.queryByTestId("bond-holdings")).toBeNull();
    expect(screen.getByText(/You have no investments yet/)).toBeTruthy();
    expect(screen.queryByTestId("bonds-left-out")).toBeNull();
  });

  it("sends a waiting coupon on, and chooses where the next ones go", async () => {
    await openPortfolio();
    await fireEvent.press(screen.getByLabelText(/^2 Year Treasury Bond 2028/));
    const coupons = await screen.findByTestId("bond-coupons");
    expect(within(coupons).getByText("Waiting for you to choose an account")).toBeTruthy();
    await fireEvent.press(within(coupons).getByText("Send it"));
    expect(await screen.findByTestId("bond-pay-out-amount")).toHaveTextContent("UGX 56,000");
    await fireEvent.press(screen.getByTestId("confirm-bond-pay-out"));
    const after = await screen.findByTestId("bond-coupons");
    expect(await within(after).findByText("Paid to your account")).toBeTruthy();

    await fireEvent.press(within(screen.getByTestId("coupon-destination")).getByTestId("account-demo-account-mtn"));
    expect(await screen.findByText(/Each coupon is sent here on its date/)).toBeTruthy();
  });

  it("asks to sell a holding with the account named, and cancels it", async () => {
    await openPortfolio();
    await fireEvent.press(screen.getByLabelText(/^5 Year Treasury Bond 2031/));
    await fireEvent.press(await screen.findByTestId("bond-sell"));
    expect(await screen.findByText("Sold at the market price")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("confirm-sell"));
    const sale = await screen.findByTestId("bond-sale");
    expect(within(sale).getByText(/Finvest's dealing desk is finding a buyer/)).toBeTruthy();
    expect(mockSell).toHaveBeenCalledTimes(1);
    expect(mockSell.mock.calls[0][0]).toMatchObject({ destinationId: "demo-account-mtn" });
    expect(screen.queryByTestId("bond-sell")).toBeNull();

    await fireEvent.press(screen.getByTestId("bond-sale-cancel"));
    expect(await screen.findByTestId("bond-sell")).toBeTruthy();
    expect(screen.queryByTestId("bond-sale")).toBeNull();
  });
});
