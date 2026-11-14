import { act, fireEvent, screen, within } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** The demo's clock, shared by investing and payments so a confirmed payment starts the term. */
const mockClock = { ms: Date.parse("2026-11-13T09:00:00Z") };
const mockRequest = jest.fn();
const mockWithdraw = jest.fn();
const mockSend = jest.fn();
const mockSample = { value: "empty" };
/** The next request's failure, if any: what the network or the backend answers instead. */
const mockFailNext: { code: string | null } = { code: null };

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
          const code = mockFailNext.code;
          if (code) {
            mockFailNext.code = null;
            const { InvestingError } = jest.requireActual("@/investing/types");
            throw new InvestingError(code);
          }
          return client.requestInvestment(request);
        },
        sendInvestment: async (request: unknown) => {
          mockSend(request);
          const code = mockFailNext.code;
          if (code) {
            mockFailNext.code = null;
            const { InvestingError } = jest.requireActual("@/investing/types");
            throw new InvestingError(code);
          }
          return client.sendInvestment(request);
        },
        withdrawEarly: async (request: unknown) => {
          mockWithdraw(request);
          const code = mockFailNext.code;
          if (code) {
            mockFailNext.code = null;
            const { InvestingError } = jest.requireActual("@/investing/types");
            throw new InvestingError(code);
          }
          return client.withdrawEarly(request);
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

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  mockSend.mockClear();
  mockSample.value = "default";
  mockFailNext.code = null;
});

async function openSend() {
  await signIn();
  await act(async () => {
    router.push("/portfolio");
  });
  await fireEvent.press(await screen.findByLabelText(/^91 Day Treasury Bill, Running/));
  await fireEvent.press(await screen.findByTestId("send-investment"));
  await screen.findByTestId("send-to");
}

async function findBrian() {
  await fireEvent.changeText(screen.getByTestId("send-to"), "1001005170");
  await fireEvent.press(screen.getByTestId("find-recipient"));
  await screen.findByTestId("recipient");
}

describe("sending an investment to someone", () => {
  it("finds the recipient by number and shows only their first name and initial", async () => {
    await openSend();
    await findBrian();
    expect(within(screen.getByTestId("recipient")).getByText("Brian K.")).toBeTruthy();
    expect(screen.getByText("Finvest account 1001005170")).toBeTruthy();
    expect(screen.getByTestId("confirm-send")).toBeDisabled();
  });

  it("says when nobody has that number", async () => {
    await openSend();
    await fireEvent.changeText(screen.getByTestId("send-to"), "1009999999");
    await fireEvent.press(screen.getByTestId("find-recipient"));
    expect(await screen.findByText(/could not find a Finvest client with that number/)).toBeTruthy();
    expect(screen.queryByTestId("recipient")).toBeNull();
  });

  it("sends part of it once and opens the transfer", async () => {
    await openSend();
    await findBrian();
    await fireEvent.changeText(screen.getByTestId("send-amount"), "1000000");
    await fireEvent.changeText(screen.getByTestId("send-message"), "For school fees");
    await fireEvent.press(screen.getByTestId("confirm-send"));

    expect(await screen.findByText("To Brian K.")).toBeTruthy();
    expect(screen.getByTestId("transfer-amount").props.children).toBe("UGX 1,000,000");
    expect(screen.getByText("For school fees")).toBeTruthy();
    expect(screen.getByTestId("open-kept")).toBeTruthy();
    expect(mockSend).toHaveBeenCalledTimes(1);
    expect(mockSend.mock.calls[0][0]).toMatchObject({ toAccountNumber: "1001005170", amount: { currency: "UGX", minor: 1_000_000 }, message: "For school fees" });
  });

  it("says why an amount was refused, and asks again under a new key", async () => {
    await openSend();
    await findBrian();
    await fireEvent.changeText(screen.getByTestId("send-amount"), "50000");
    await fireEvent.press(screen.getByTestId("confirm-send"));
    expect(await screen.findByText(/below the smallest amount for this product/)).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("send-amount"), "1000000");
    await fireEvent.press(screen.getByTestId("confirm-send"));
    await screen.findByText("To Brian K.");
    expect(mockSend.mock.calls[0][0].idempotencyKey).not.toBe(mockSend.mock.calls[1][0].idempotencyKey);
  });

  it("asks again under the same key after a lost answer, with the details locked", async () => {
    await openSend();
    await findBrian();
    await fireEvent.changeText(screen.getByTestId("send-amount"), "1000000");
    mockFailNext.code = "unavailable";
    await fireEvent.press(screen.getByTestId("confirm-send"));
    expect(await screen.findByText(/so you will not send twice/)).toBeTruthy();
    expect(screen.getByTestId("send-amount").props.editable).toBe(false);
    await fireEvent.press(screen.getByTestId("confirm-send"));
    await screen.findByText("To Brian K.");
    expect(mockSend.mock.calls[0][0].idempotencyKey).toBe(mockSend.mock.calls[1][0].idempotencyKey);
  });

  it("lists what was sent and received", async () => {
    await signIn();
    await act(async () => {
      router.push("/transfers");
    });
    expect(await screen.findByText("From Amara N.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("transfer-demo-transfer-received"));
    expect(await screen.findByText("Happy birthday!")).toBeTruthy();
    expect(screen.getByTestId("open-received")).toBeTruthy();
  });
});
