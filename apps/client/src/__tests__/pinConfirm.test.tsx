import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { appPinApplies } from "@/auth/pin";
import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { passkeysSupported } from "@/auth/webauthn";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

const mockAdded = jest.fn();
const mockWithdraw = jest.fn();

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/payouts/stubPayoutsClient", () => {
  const actual = jest.requireActual("@/payouts/stubPayoutsClient");
  return {
    ...actual,
    createStubPayoutsClient: () => {
      const client = actual.createStubPayoutsClient({ delayMs: 0, sample: "default" });
      return {
        ...client,
        addAccount: async (account: unknown) => {
          mockAdded(account);
          return client.addAccount(account);
        },
      };
    },
  };
});

jest.mock("@/investing/stubInvestingClient", () => {
  const actual = jest.requireActual("@/investing/stubInvestingClient");
  return {
    ...actual,
    createStubInvestingClient: () => {
      const client = actual.createStubInvestingClient({ delayMs: 0, sample: "default" });
      return {
        ...client,
        withdrawEarly: async (request: unknown) => {
          mockWithdraw(request);
          return client.withdrawEarly(request);
        },
      };
    },
  };
});

jest.mock("@/auth/webauthn", () => ({
  ...jest.requireActual("@/auth/webauthn"),
  passkeysSupported: jest.fn(() => false),
}));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.setTimeout(30_000);

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  mockAdded.mockClear();
  mockWithdraw.mockClear();
  jest.mocked(passkeysSupported).mockReturnValue(false);
});

afterEach(() => {
  jest.mocked(appPinApplies).mockReturnValue(false);
});

async function typePin(pin: string) {
  for (const digit of pin) {
    await fireEvent.press(screen.getByTestId(`pin-key-${digit}`));
  }
}

async function logIn(withPin: boolean) {
  jest.mocked(appPinApplies).mockReturnValue(withPin);
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  if (withPin) {
    await screen.findByText("Create a 5 digit PIN");
    await typePin("48151");
    await typePin("48151");
  }
  await screen.findByTestId("home");
}

async function fillNewAccount() {
  await act(async () => {
    router.push("/payouts/add");
  });
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "701555013");
  await fireEvent.changeText(screen.getByTestId("holder-name-input"), "Amara Nakato");
  await fireEvent.press(screen.getByTestId("payout-account-save"));
}

describe("confirming a new payout account with the PIN", () => {
  it("asks for the PIN on a phone, counts a wrong one, and keeps the form on cancel", async () => {
    await logIn(true);
    await fillNewAccount();
    await screen.findByText("Confirm with your PIN");
    expect(screen.getByText("Enter your PIN to add this payout account")).toBeTruthy();

    await typePin("90817");
    await screen.findByText("That PIN is not right. 4 tries left before this phone is signed out.");
    await fireEvent.press(screen.getByTestId("pin-confirm-cancel"));
    // Back on the form, with what was typed, and nothing was sent.
    expect(await screen.findByDisplayValue("Amara Nakato")).toBeTruthy();
    expect(mockAdded).not.toHaveBeenCalled();

    await fireEvent.press(screen.getByTestId("payout-account-save"));
    await screen.findByText("Confirm with your PIN");
    await typePin("48151");
    await screen.findByText(/We texted a code/);
    expect(mockAdded).toHaveBeenCalledTimes(1);
    expect(mockAdded.mock.calls[0][0].pinConfirmation).toMatch(/^demo-payout_account-/);
  });

  it("asks for nothing where the app has no PIN", async () => {
    await logIn(false);
    await fillNewAccount();
    await screen.findByText(/We texted a code/);
    expect(screen.queryByText("Confirm with your PIN")).toBeNull();
    expect(mockAdded.mock.calls[0][0].pinConfirmation).toBeNull();
  });
});

describe("confirming an early withdrawal with the PIN", () => {
  async function openWithdraw() {
    await act(async () => {
      router.push("/portfolio");
    });
    await fireEvent.press(await screen.findByLabelText(/^91 Day Treasury Bill, Running/));
    await fireEvent.press(await screen.findByTestId("withdraw-early"));
    await screen.findByTestId("withdraw-amount");
  }

  it("asks for the PIN before the withdrawal is sent, and sends nothing on cancel", async () => {
    await logIn(true);
    await openWithdraw();
    await fireEvent.press(screen.getByTestId("confirm-withdraw"));
    await screen.findByText("Enter your PIN to confirm this withdrawal");
    await fireEvent.press(screen.getByTestId("pin-confirm-cancel"));
    await screen.findByTestId("withdraw-amount");
    expect(mockWithdraw).not.toHaveBeenCalled();

    await fireEvent.press(screen.getByTestId("confirm-withdraw"));
    await screen.findByText("Enter your PIN to confirm this withdrawal");
    await typePin("48151");
    expect(await screen.findByText(/goes to your account, and we tell you when it is sent/)).toBeTruthy();
    expect(mockWithdraw).toHaveBeenCalledTimes(1);
    expect(mockWithdraw.mock.calls[0][0].pinConfirmation).toMatch(/^demo-withdrawal-/);
  });
});
