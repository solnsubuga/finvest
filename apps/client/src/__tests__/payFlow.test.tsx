import { Share } from "react-native";
import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { DEMO_PAYABLE_ID } from "@/payments/stubPaymentsClient";
import { PaymentError } from "@/payments/types";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** The demo's clock, moved by hand so each state can be looked at. */
const mockClock = { ms: Date.parse("2026-11-13T12:31:00Z") };
const mockPay = jest.fn();
/** The paying number is the account's own, so tests choose the demo's outcome here. */
const mockScenario = { code: "00" };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }),
  };
});

jest.mock("@/payments/stubPaymentsClient", () => {
  const actual = jest.requireActual("@/payments/stubPaymentsClient");
  return {
    ...actual,
    createStubPaymentsClient: () => {
      const client = actual.createStubPaymentsClient({
        delayMs: 0,
        now: () => mockClock.ms,
        scenarioFor: () => mockScenario.code,
      });
      return {
        ...client,
        payWithMobileMoney: async (request: unknown) => {
          mockPay(request);
          return client.payWithMobileMoney(request);
        },
      };
    },
  };
});

// The preset's mock of the native module returns nothing. Tests need real, distinct keys.
jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("@/payments/pollInterval", () => ({ POLL_MS: 25 }));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function openPayScreen() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByTestId("home");
  await fireEvent.press(screen.getByLabelText("Invest"));
  await fireEvent.press(await screen.findByTestId("demo-pay"));
  await screen.findByText("Pay UGX 1,000,000");
}

async function advance(ms: number) {
  await act(async () => {
    mockClock.ms += ms;
  });
}

beforeEach(() => {
  mockPay.mockClear();
  mockScenario.code = "00";
});

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("paying for an investment by mobile money", () => {
  it("shows the amount and the account's own number, which cannot be changed", async () => {
    await openPayScreen();
    expect(screen.getByText("91 Day Treasury Bill")).toBeTruthy();
    expect(screen.getByText("UGX 1,000,000")).toBeTruthy();
    expect(screen.getByText("+256 772 123 456")).toBeTruthy();
    expect(screen.queryByTestId("phone-input")).toBeNull();
    expect(screen.getByTestId("network-mtn")).toBeChecked();
    expect(screen.getByTestId("network-airtel")).not.toBeChecked();
  });

  it("goes from the prompt to the receipt when the payment is approved", async () => {
    await openPayScreen();
    await fireEvent.press(screen.getByTestId("pay-submit"));

    await screen.findByText("Approve on your phone");
    await advance(2_000);
    await screen.findByText(/MTN has sent a prompt to \+256 77• ••• •56/);
    expect(screen.getByText("Waiting for your PIN")).toBeTruthy();
    expect(screen.getByText(/Dial \*165#/)).toBeTruthy();

    await advance(6_000);
    await screen.findByText("Completed");
    expect(screen.getByText("Paid into 91 Day Treasury Bill")).toBeTruthy();
    expect(screen.getByText("13 Nov 2026, 15:31")).toBeTruthy();
    expect(screen.getByText("MTN Mobile Money")).toBeTruthy();
    expect(screen.getByText("+256 77• ••• •56")).toBeTruthy();

    const share = jest.spyOn(Share, "share").mockResolvedValue({ action: "sharedAction", activityType: undefined });
    await fireEvent.press(screen.getByTestId("receipt-share"));
    expect(share).toHaveBeenCalledTimes(1);
    const message = share.mock.calls[0]![0] as { message: string };
    expect(message.message).toMatch(/^Finvest receipt: UGX [\d,]+ paid into 91 Day Treasury Bill/);
    expect(message.message).toContain("Number: +256 77• ••• •56");
    expect(message.message).toMatch(/Finvest reference: \S+/);
    share.mockRestore();

    await fireEvent.press(screen.getByTestId("receipt-done"));
    await screen.findByTestId("home");
  });

  it("repeats the same request under the same key after a lost try", async () => {
    await openPayScreen();
    mockPay.mockImplementationOnce(() => {
      throw new PaymentError("unavailable");
    });
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("We could not reach Finvest. Check your connection and try again.");

    // The first request may have been saved, so the network can no longer be changed.
    expect(screen.getByText(/Your first request may already be with MTN/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("network-airtel"));
    expect(screen.getByTestId("network-mtn")).toBeChecked();
    expect(screen.getByTestId("network-airtel")).toBeDisabled();

    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");
    expect(mockPay).toHaveBeenCalledTimes(2);
    expect(mockPay.mock.calls[1][0]).toEqual(mockPay.mock.calls[0][0]);
  });

  it("leaves the network open after a refusal, because nothing was saved", async () => {
    await openPayScreen();
    mockPay.mockImplementationOnce(() => {
      throw new PaymentError("already_paying");
    });
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText(/A payment for this is already under way/);
    await fireEvent.press(screen.getByTestId("network-airtel"));
    expect(screen.getByTestId("network-airtel")).toBeChecked();
  });

  it("takes the client to the payment already under way instead of paying again", async () => {
    await openPayScreen();
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");

    // A new visit to the pay screen makes a new key, so only the open payment can stop it.
    await act(async () => {
      router.push(`/pay/${DEMO_PAYABLE_ID}`);
    });
    await screen.findByText("Pay UGX 1,000,000");
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");
    expect(screen.queryByText(/already under way/)).toBeNull();
    expect(mockPay).toHaveBeenCalledTimes(2);
    expect(mockPay.mock.calls[1][0].idempotencyKey).not.toBe(mockPay.mock.calls[0][0].idempotencyKey);
  });

  it("pays from Airtel when the client chooses it, with Airtel's own instructions", async () => {
    await openPayScreen();
    await fireEvent.press(screen.getByTestId("network-airtel"));
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");
    await advance(2_000);
    await screen.findByText(/Airtel has sent a prompt to \+256 77• ••• •56/);
    expect(screen.getByText(/Dial \*185#/)).toBeTruthy();
    expect(mockPay.mock.calls[0][0]).toMatchObject({ network: "airtel", payer: "+256772123456" });
  });

  it("says what the network said when the payment fails, and lets the client try again", async () => {
    mockScenario.code = "02";
    await openPayScreen();
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");

    await advance(8_000);
    await screen.findByText("Your payment did not go through");
    expect(
      screen.getByText("MTN says there was not enough money on +256 77• ••• •56."),
    ).toBeTruthy();
    expect(screen.getByText("Nothing was taken")).toBeTruthy();
    expect(screen.getByText("Insufficient balance")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("pay-retry"));
    await screen.findByText("Pay UGX 1,000,000");
    mockScenario.code = "00";
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");
    // A new visit to the pay screen is a new intent, so it carries a new key.
    const keys = new Set(mockPay.mock.calls.map(([request]) => request.idempotencyKey));
    expect(keys.size).toBe(2);
  });

  it("tells the client not to pay again while an unclear payment is checked", async () => {
    mockScenario.code = "04";
    await openPayScreen();
    await fireEvent.press(screen.getByTestId("pay-submit"));
    await screen.findByText("Approve on your phone");

    await advance(5_000);
    await screen.findByText("We are checking your payment");
    expect(screen.getByText(/Do not pay again/)).toBeTruthy();
    expect(screen.queryByText("Try again")).toBeNull();
    expect(screen.queryByTestId("pay-submit")).toBeNull();

    await advance(10_000);
    await screen.findByText("Completed");
  });
});
