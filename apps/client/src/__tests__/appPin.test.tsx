import { AppState, AppStateStatus } from "react-native";
import { act, fireEvent, screen, waitFor } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { appPinApplies, LOCK_AFTER_MS } from "@/auth/pin";
import { DEMO_CODE, DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { passkeysSupported } from "@/auth/webauthn";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/auth/webauthn", () => ({
  ...jest.requireActual("@/auth/webauthn"),
  passkeysSupported: jest.fn(() => false),
}));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

const listeners: ((state: AppStateStatus) => void)[] = [];

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  jest.mocked(appPinApplies).mockReturnValue(true);
  jest.mocked(passkeysSupported).mockReturnValue(false);
  listeners.length = 0;
  jest.spyOn(AppState, "addEventListener").mockImplementation((_type, handler) => {
    listeners.push(handler as (state: AppStateStatus) => void);
    return { remove: jest.fn() } as unknown as ReturnType<typeof AppState.addEventListener>;
  });
});

afterEach(() => {
  jest.mocked(appPinApplies).mockReturnValue(false);
  jest.restoreAllMocks();
});

async function typePin(pin: string) {
  for (const digit of pin) {
    await fireEvent.press(screen.getByTestId(`pin-key-${digit}`));
  }
}

async function logIn() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await screen.findByText("Welcome back");
  await fireEvent.changeText(screen.getByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
}

async function background(forMs: number) {
  const now = Date.now();
  await act(async () => listeners.forEach((listener) => listener("background")));
  jest.spyOn(Date, "now").mockReturnValue(now + forMs);
  await act(async () => listeners.forEach((listener) => listener("active")));
}

describe("the app PIN on a phone", () => {
  it("is chosen after a log in, typed twice, and refuses a simple one", async () => {
    await logIn();
    await screen.findByText("Create a 5 digit PIN");
    await typePin("12345");
    expect(screen.getByText(/That PIN is too easy to guess/)).toBeTruthy();

    await typePin("48151");
    await screen.findByText("Type your PIN again");
    await typePin("48152");
    await screen.findByText("Those PINs did not match. Choose your PIN again.");

    await typePin("48151");
    await typePin("48151");
    await screen.findByTestId("home");
  });

  it("opens the app again after a while in the background", async () => {
    await logIn();
    await screen.findByText("Create a 5 digit PIN");
    await typePin("48151");
    await typePin("48151");
    await screen.findByTestId("home");

    // A short look at another app does not lock it.
    await background(LOCK_AFTER_MS - 1000);
    expect(screen.queryByText("Enter your PIN to open the app on this phone")).toBeNull();

    await background(LOCK_AFTER_MS);
    await screen.findByText("Enter your PIN to open the app on this phone");
    expect(screen.getByText("Welcome back, Amara")).toBeTruthy();
    await typePin("90817");
    await screen.findByText("That PIN is not right. 4 tries left before this phone is signed out.");
    await typePin("48151");
    await screen.findByTestId("home");
  });

  it("signs the phone out after five wrong PINs", async () => {
    await logIn();
    await screen.findByText("Create a 5 digit PIN");
    await typePin("48151");
    await typePin("48151");
    await screen.findByTestId("home");
    await background(LOCK_AFTER_MS);
    await screen.findByText("Enter your PIN to open the app on this phone");
    for (let tries = 0; tries < 5; tries += 1) {
      await typePin("90817");
    }
    await screen.findByText("Signed out for your safety");
    await fireEvent.press(screen.getByTestId("pin-log-in-again"));
    await screen.findByText("Welcome back");
  });

  it("resets a forgotten PIN by logging in again", async () => {
    await logIn();
    await screen.findByText("Create a 5 digit PIN");
    await typePin("48151");
    await typePin("48151");
    await screen.findByTestId("home");
    await background(LOCK_AFTER_MS);
    await fireEvent.press(await screen.findByTestId("pin-forgot"));
    await screen.findByText("Reset your PIN");
    await fireEvent.press(screen.getByTestId("pin-log-in-again"));
    await screen.findByText("Welcome back");
  });
});

describe("signing up", () => {
  it("asks for a PIN, then offers a passkey where the device can make one", async () => {
    jest.mocked(passkeysSupported).mockReturnValue(true);
    await renderRouter("./src/app", { initialUrl: "/" });
    await fireEvent.press(await screen.findByText("Create an account"));
    await screen.findByText("Create your account");
    await fireEvent.changeText(screen.getByTestId("phone-input"), "701555013");
    await fireEvent.changeText(screen.getByTestId("email-input"), "new@example.com");
    await fireEvent.press(screen.getByTestId("terms-checkbox"));
    await fireEvent.press(screen.getByTestId("signup-submit"));
    await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));
    await screen.findByText("Create a password");
    expect(screen.getByText("2 of 4")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("password-input"), "green matooke sunday");
    await fireEvent.changeText(screen.getByTestId("password-confirm-input"), "green matooke sunday");
    await fireEvent.press(screen.getByTestId("password-submit"));

    await screen.findByText("Create a 5 digit PIN");
    expect(screen.getByText("3 of 4")).toBeTruthy();
    expect(screen.getByText("Next you can add a passkey for your second step.")).toBeTruthy();
    await typePin("48151");
    await typePin("48151");

    await screen.findByRole("header", { name: "Add a passkey" });
    expect(screen.getByText("4 of 4")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("offer-not-now"));
    await waitFor(() => expect(screen.getByTestId("home")).toBeTruthy());
  });
});
