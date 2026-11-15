import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { DEMO_CARD_DECLINED_ID, DEMO_CARD_LIMIT_ID, DEMO_CARDS_PAUSED_ID } from "@/payments/stubPaymentsClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** The demo's clock, moved by hand so each state can be looked at. */
const mockClock = { ms: Date.parse("2026-11-13T12:31:00Z") };
const mockPay = jest.fn();

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
      });
      return {
        ...client,
        payWithCard: async (request: unknown) => {
          mockPay(request);
          return client.payWithCard(request);
        },
      };
    },
  };
});

// The preset's mock of the native module returns nothing. Tests need real, distinct keys.
jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

const mockComplain = jest.fn();

jest.mock("@/support/stubSupportClient", () => {
  const actual = jest.requireActual("@/support/stubSupportClient");
  return {
    ...actual,
    createStubSupportClient: () => {
      const client = actual.createStubSupportClient({ delayMs: 0, now: () => mockClock.ms });
      return {
        ...client,
        makeComplaint: async (complaint: unknown) => {
          mockComplain(complaint);
          return client.makeComplaint(complaint);
        },
      };
    },
  };
});

const mockOpened: string[] = [];
jest.mock("@/payments/openCardPage", () => ({
  openCardPage: (url: string) => mockOpened.push(url),
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

async function openPayScreen(payableId?: string) {
  await signIn();
  if (payableId) {
    await act(async () => router.push(`/pay/${payableId}`));
  } else {
    await fireEvent.press(screen.getByLabelText("Invest"));
    await fireEvent.press(await screen.findByTestId("demo-pay"));
  }
  await screen.findByTestId("method-card");
}

async function advance(ms: number) {
  await act(async () => {
    mockClock.ms += ms;
  });
}

beforeEach(() => {
  mockPay.mockClear();
  mockOpened.length = 0;
});

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("paying for an investment by card", () => {
  it("shows the card charge on top and the total before anything is sent", async () => {
    await openPayScreen();
    await fireEvent.press(screen.getByTestId("method-card"));
    const breakdown = await screen.findByTestId("card-charge");
    expect(breakdown).toHaveTextContent(/Investment.*UGX 1,000,000/);
    expect(breakdown).toHaveTextContent(/Card charge.*UGX 35,000/);
    expect(breakdown).toHaveTextContent(/You pay.*UGX 1,035,000/);
    expect(screen.getByText("Pay UGX 1,035,000 by card")).toBeTruthy();
    expect(mockPay).not.toHaveBeenCalled();
  });

  it("goes to Pesapal's page, back, and on to the receipt once Pesapal confirms", async () => {
    await openPayScreen();
    await fireEvent.press(screen.getByTestId("method-card"));
    await fireEvent.press(await screen.findByText("Pay UGX 1,035,000 by card"));
    expect(mockPay).toHaveBeenCalledTimes(1);

    await screen.findByText("Finish on Pesapal's secure page");
    await advance(2_000);
    await fireEvent.press(await screen.findByText("Open the secure page"));
    expect(mockOpened).toHaveLength(1);
    await screen.findByText("Waiting for Pesapal");

    // Pesapal sends the client back to our return address.
    await act(async () => router.replace(mockOpened[0]!));
    await screen.findByText("Waiting for Pesapal");
    expect(screen.queryByText("Open the secure page")).toBeNull();

    await advance(4_000);
    await screen.findByText("Completed");
    expect(screen.getByText("Visa card ending 0010")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("card-done"));
    await screen.findByTestId("home");
  });

  it("says a declined card took no money and offers another way", async () => {
    await openPayScreen(DEMO_CARD_DECLINED_ID);
    await fireEvent.press(screen.getByTestId("method-card"));
    await fireEvent.press(await screen.findByText(/by card$/));
    await advance(2_000);
    await fireEvent.press(await screen.findByText("Open the secure page"));
    await act(async () => router.replace(mockOpened[0]!));
    await advance(4_000);
    await screen.findByText("Your card was declined");
    await fireEvent.press(screen.getByTestId("card-pay-another-way"));
    expect(await screen.findByTestId("method-mobile-money")).toBeChecked();
  });

  it("tries a declined card again with card already chosen", async () => {
    await openPayScreen(DEMO_CARD_DECLINED_ID);
    await fireEvent.press(screen.getByTestId("method-card"));
    await fireEvent.press(await screen.findByText(/by card$/));
    await advance(2_000);
    await fireEvent.press(await screen.findByText("Open the secure page"));
    await act(async () => router.replace(mockOpened[0]!));
    await advance(4_000);
    await screen.findByText("Your card was declined");
    await fireEvent.press(screen.getByTestId("card-retry"));
    expect(await screen.findByTestId("method-card")).toBeChecked();
    expect(screen.getByTestId("card-charge")).toBeTruthy();
  });

  it("refuses a card above the limit and while cards are paused, before anything is sent", async () => {
    await openPayScreen(DEMO_CARD_LIMIT_ID);
    // The card choice cannot be picked, and the screen says why before anything is pressed.
    expect(screen.getByTestId("method-card")).toBeDisabled();
    await screen.findByText("Cards take up to UGX 5,000,000 a payment");

    await act(async () => router.replace(`/pay/${DEMO_CARDS_PAUSED_ID}`));
    await screen.findByText("Card payments are paused");
    expect(screen.getByTestId("method-card")).toBeDisabled();
    expect(mockPay).not.toHaveBeenCalled();
  });
});
