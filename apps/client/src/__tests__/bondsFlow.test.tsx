import { act, fireEvent, screen, within } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** 16 November 2026 in Kampala: the day of the worked example in docs/bonds.md. */
const mockClock = { ms: Date.parse("2026-11-16T09:00:00Z") };
const mockBuy = jest.fn();
const mockFailBond: { value: boolean } = { value: false };

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
    createStubInvestingClient: () => actual.createStubInvestingClient({ delayMs: 0, now: () => mockClock.ms, sample: "empty" }),
  };
});

jest.mock("@/bonds/stubBondsClient", () => {
  const actual = jest.requireActual("@/bonds/stubBondsClient");
  return {
    ...actual,
    createStubBondsClient: () => {
      const client = actual.createStubBondsClient({ delayMs: 0, now: () => mockClock.ms });
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
