import { AppState, AppStateStatus } from "react-native";
import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { appPinApplies, LOCK_AFTER_MS } from "@/auth/pin";
import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { passkeysSupported } from "@/auth/webauthn";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** The phone's fingerprint, as the test sets it up: whether there is one, and what it answers. */
const mockPhone = { enrolled: true, answers: true, kept: null as string | null, asked: 0 };

jest.mock("@/auth/biometric", () => ({
  biometricOnThisPhone: async () => mockPhone.enrolled,
  keepUnlockKey: async (key: string) => {
    mockPhone.asked += 1;
    if (!mockPhone.answers) throw new Error("user_cancel");
    mockPhone.kept = key;
  },
  unlockKeyKept: async () => mockPhone.kept !== null,
  readUnlockKey: async () => {
    mockPhone.asked += 1;
    return mockPhone.answers ? mockPhone.kept : null;
  },
  forgetUnlockKey: async () => {
    mockPhone.kept = null;
  },
}));

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
  Object.assign(mockPhone, { enrolled: true, answers: true, kept: null, asked: 0 });
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

async function signedInWithPin() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByText("Create a 5 digit PIN");
  await typePin("48151");
  await typePin("48151");
  await screen.findByText("Welcome, Amara");
}

async function openFingerprint() {
  await act(async () => {
    router.push("/security");
  });
  await fireEvent.press(await screen.findByTestId("open-fingerprint"));
  await screen.findByText("Unlock with fingerprint or face");
}

async function lock() {
  const now = Date.now();
  await act(async () => listeners.forEach((listener) => listener("background")));
  jest.spyOn(Date, "now").mockReturnValue(now + LOCK_AFTER_MS);
  await act(async () => listeners.forEach((listener) => listener("active")));
}

async function turnOn() {
  await openFingerprint();
  await fireEvent.press(screen.getByTestId("fingerprint-on"));
  await screen.findByText("Enter your PIN to turn on fingerprint or face unlock");
  await typePin("48151");
  await screen.findByText("Fingerprint or face unlock is on");
  await fireEvent.press(screen.getByTestId("done"));
  await screen.findByLabelText("Fingerprint or face unlock: on");
}

describe("unlocking with a fingerprint or face", () => {
  it("is turned on with the PIN and then opens the locked app by itself", async () => {
    await signedInWithPin();
    await openFingerprint();
    expect(screen.getByText(/Your PIN is never kept on this phone/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("fingerprint-on"));
    await screen.findByText("Enter your PIN to turn on fingerprint or face unlock");
    await typePin("90817");
    await screen.findByText("That PIN is not right. 4 tries left before this phone is signed out.");
    await typePin("48151");
    await screen.findByText("Fingerprint or face unlock is on");
    expect(mockPhone.kept).toMatch(/^demo-unlock-key/);
    await fireEvent.press(screen.getByTestId("done"));
    expect(await screen.findByLabelText("Fingerprint or face unlock: on")).toBeTruthy();

    await lock();
    // The prompt comes up as the lock screen opens, and the right finger opens the app.
    await screen.findByText("Welcome, Amara");
    expect(screen.queryByText("Enter your PIN to open the app on this phone")).toBeNull();
    expect(mockPhone.asked).toBe(2);
  });

  it("leaves the PIN pad and a button when the client cancels the prompt", async () => {
    await signedInWithPin();
    await turnOn();
    mockPhone.answers = false;
    await lock();
    await screen.findByText("Enter your PIN to open the app on this phone");
    expect(await screen.findByTestId("unlock-biometric")).toBeTruthy();
    mockPhone.answers = true;
    await fireEvent.press(screen.getByTestId("unlock-biometric"));
    await screen.findByText("Welcome, Amara");
  });

  it("goes back to the PIN once the server has forgotten the key", async () => {
    await signedInWithPin();
    await turnOn();
    mockPhone.kept = "a-key-the-server-never-gave";
    await lock();
    await screen.findByText("Fingerprint or face unlock is off for this phone. Enter your PIN.");
    expect(screen.queryByTestId("unlock-biometric")).toBeNull();
    expect(mockPhone.kept).toBeNull();
    await typePin("48151");
    await screen.findByText("Welcome, Amara");
  });

  it("stays off when the phone does not confirm the fingerprint", async () => {
    await signedInWithPin();
    mockPhone.answers = false;
    await openFingerprint();
    await fireEvent.press(screen.getByTestId("fingerprint-on"));
    await typePin("48151");
    await screen.findByText(/did not confirm your fingerprint or face, so unlock is still off/);
    expect(screen.getByTestId("fingerprint-on")).toBeTruthy();
    expect(mockPhone.kept).toBeNull();
  });

  it("is turned off from Sign in and security", async () => {
    await signedInWithPin();
    await turnOn();
    await fireEvent.press(await screen.findByTestId("open-fingerprint"));
    await fireEvent.press(await screen.findByTestId("fingerprint-off"));
    await screen.findByText("Fingerprint or face unlock is off");
    expect(mockPhone.kept).toBeNull();
    await fireEvent.press(screen.getByTestId("done"));
    expect(await screen.findByLabelText("Fingerprint or face unlock: off")).toBeTruthy();
    await lock();
    await screen.findByText("Enter your PIN to open the app on this phone");
    expect(screen.queryByTestId("unlock-biometric")).toBeNull();
  });

  it("is not offered on a phone with no fingerprint or face set up", async () => {
    mockPhone.enrolled = false;
    await signedInWithPin();
    await act(async () => {
      router.push("/security");
    });
    await screen.findByTestId("open-devices");
    expect(screen.queryByTestId("open-fingerprint")).toBeNull();
  });
});
