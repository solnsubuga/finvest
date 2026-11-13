import { act, fireEvent, screen, within } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";

/** The demo's clock, shared by investing and payments so a confirmed payment starts the term. */
const mockClock = { ms: Date.parse("2026-11-13T09:00:00Z") };
const mockRequest = jest.fn();
const mockSample = { value: "empty" };

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
    createStubInvestingClient: () => {
      const client = actual.createStubInvestingClient({ delayMs: 0, now: () => mockClock.ms, sample: mockSample.value });
      return {
        ...client,
        requestInvestment: async (request: unknown) => {
          mockRequest(request);
          return client.requestInvestment(request);
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
  await screen.findByText("Welcome, Amara");
}

async function quoteFor(amount: string) {
  await fireEvent.changeText(await screen.findByTestId("amount"), amount);
  // The quote is asked for once typing pauses.
  await screen.findByText(/You invest|below the smallest/, undefined, { timeout: 2000 });
}

jest.setTimeout(20_000);

beforeEach(() => {
  mockRequest.mockClear();
  mockSample.value = "empty";
});

describe("investing", () => {
  it("lists products with the rate after tax and never the tax", async () => {
    await signIn();
    await fireEvent.press(screen.getByLabelText("Invest"));
    const bill = await screen.findByTestId("product-TREA-91");
    expect(within(bill).getByText("10.63%")).toBeTruthy();
    expect(within(bill).getByText("From UGX 100,000")).toBeTruthy();
    expect(within(screen.getByTestId("product-USD-BOND")).getByText("Paused for new investments")).toBeTruthy();
    expect(screen.queryByText(/15%/)).toBeNull();
    expect(screen.queryByText(/12\.5%/)).toBeNull();
  });

  it("works out what an amount earns, from the backend's quote", async () => {
    await signIn();
    await fireEvent.press(screen.getByLabelText("Invest"));
    await fireEvent.press(await screen.findByTestId("product-TREA-91"));
    await quoteFor("1000000");
    const quote = await screen.findByTestId("quote");
    expect(within(quote).getByLabelText("Interest after tax: UGX 26,490")).toBeTruthy();
    expect(within(quote).getByLabelText("You get back: UGX 1,026,490")).toBeTruthy();
    expect(within(quote).getByLabelText("Matures on: 12 Feb 2027")).toBeTruthy();
    expect(screen.getByTestId("amount").props.value).toBe("1,000,000");
  });

  it("refuses an amount below the minimum", async () => {
    await signIn();
    await fireEvent.press(screen.getByLabelText("Invest"));
    await fireEvent.press(await screen.findByTestId("product-TREA-182"));
    await quoteFor("50000");
    expect(await screen.findByText("That is below the smallest amount for this product.")).toBeTruthy();
    expect(screen.getByTestId("review").props.accessibilityState.disabled).toBe(true);
  });

  it("reviews, confirms once, and pays for what was confirmed", async () => {
    await signIn();
    await fireEvent.press(screen.getByLabelText("Invest"));
    await fireEvent.press(await screen.findByTestId("product-TREA-91"));
    await quoteFor("1000000");
    await screen.findByTestId("quote");
    await fireEvent.press(screen.getByTestId("review"));
    await screen.findByText("Review your investment");
    await fireEvent.press(screen.getByTestId("at-maturity-pay_out"));
    await fireEvent.press(screen.getByTestId("confirm"));
    await screen.findByText("Pay UGX 1,000,000");
    expect(mockRequest).toHaveBeenCalledTimes(1);
    expect(mockRequest.mock.calls[0][0]).toMatchObject({
      productCode: "TREA-91",
      amount: { currency: "UGX", minor: 1_000_000 },
      atMaturity: "pay_out",
    });

    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");
    await act(async () => {
      mockClock.ms += 8_000;
    });
    await screen.findByText("Completed");
  });

  it("shows a new client how to start", async () => {
    await signIn();
    // Portfolio is in the wide sidebar, not the phone tabs, so the test goes there directly.
    await act(async () => {
      router.push("/portfolio");
    });
    expect(await screen.findByText("You have no investments yet. Pick a product to make your first one.")).toBeTruthy();
  });

  it("lists running investments and opens one with its interest after tax", async () => {
    mockSample.value = "default";
    await signIn();
    // Portfolio is in the wide sidebar, not the phone tabs, so the test goes there directly.
    await act(async () => {
      router.push("/portfolio");
    });
    const card = await screen.findByLabelText(/^91 Day Treasury Bill, Running/);
    expect(within(card).getByText("Running")).toBeTruthy();
    expect(within(card).getByText("UGX 5,123,715")).toBeTruthy();
    await fireEvent.press(card);
    expect(await screen.findByLabelText("Interest so far, after tax: UGX 123,715")).toBeTruthy();
    expect(screen.getByLabelText("Interest at maturity, after tax: UGX 132,448")).toBeTruthy();
    expect(screen.getByLabelText("When the term ends: Roll over")).toBeTruthy();
    expect(screen.queryByText(/tax:|15%/)).toBeNull();
  });
});
