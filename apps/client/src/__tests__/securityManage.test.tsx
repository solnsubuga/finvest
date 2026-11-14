import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** Minutes added to the stub's clock, so a test can let the session grow old. */
const mockClock = { minutes: 0 };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () =>
      actual.createStubAuthClient({
        delayMs: 0,
        now: () => Date.now() + mockClock.minutes * 60_000,
      }),
  };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

beforeEach(() => {
  mockClock.minutes = 0;
});

/** Logs in as the demo client with a passkey, an authenticator app and two other devices. */
async function openSecurity() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByText("Welcome, Amara");
  await fireEvent.press(screen.getByLabelText("Profile"));
  await fireEvent.press(await screen.findByTestId("open-security"));
  await screen.findByTestId("on-passkey");
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("Devices", () => {
  it("lists this device and the others, and signs them out", async () => {
    await openSecurity();
    await fireEvent.press(screen.getByTestId("open-devices"));
    expect(await screen.findByText("This device")).toBeTruthy();
    expect(screen.getByText(/Signed in today at .* with a passkey/)).toBeTruthy();
    expect(screen.getByText("Finvest app on iPhone")).toBeTruthy();
    expect(screen.getByText("Chrome on Windows")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("sign-out-device_demo_1"));
    await screen.findByText("Finvest app on iPhone is signed out.");
    expect(screen.queryByTestId("device-device_demo_1")).toBeNull();

    await fireEvent.press(screen.getByTestId("sign-out-others"));
    await screen.findByText("Sign out every other device?");
    await fireEvent.press(screen.getByTestId("confirm-sign-out-others"));
    await screen.findByText("1 other device is signed out.");
    expect(await screen.findByTestId("no-other-devices")).toBeTruthy();
    expect(screen.getByText("This device")).toBeTruthy();
  });
});

describe("Passkeys", () => {
  it("removes passkeys one at a time, and the last one turns the row back to Add", async () => {
    await openSecurity();
    await fireEvent.press(screen.getByTestId("on-passkey"));
    await screen.findByText("iPhone");
    expect(screen.getByText(/Not used to log in yet/)).toBeTruthy();

    await fireEvent.press(screen.getAllByText("Remove")[0]!);
    expect(screen.getByText("You will no longer be able to log in with this passkey.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("confirm-remove-passkey"));
    await screen.findByText(/The passkey "iPhone" is removed/);
    expect(screen.queryByTestId("passkey-passkey_demo_1")).toBeNull();

    await fireEvent.press(screen.getByText("Remove"));
    expect(screen.getByText(/This is your only passkey/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("confirm-remove-passkey"));
    expect(await screen.findByTestId("no-passkeys")).toBeTruthy();

    await fireEvent.press(screen.getByLabelText("Back"));
    expect(await screen.findByTestId("setup-passkey")).toBeTruthy();
  });

  it("keeps the passkey when the client changes their mind", async () => {
    await openSecurity();
    await fireEvent.press(screen.getByTestId("on-passkey"));
    await screen.findByText("iPhone");
    await fireEvent.press(screen.getAllByText("Remove")[0]!);
    await fireEvent.press(screen.getByText("Keep it"));
    expect(screen.queryByTestId("confirm-remove-passkey")).toBeNull();
    expect(screen.getByTestId("passkey-passkey_demo_1")).toBeTruthy();
  });

  it("asks for a fresh log in when the session is too old to remove one", async () => {
    await openSecurity();
    await fireEvent.press(screen.getByTestId("on-passkey"));
    await screen.findByText("iPhone");
    mockClock.minutes = 11;
    await fireEvent.press(screen.getAllByText("Remove")[0]!);
    await fireEvent.press(screen.getByTestId("confirm-remove-passkey"));
    expect(await screen.findByText("Log in again first")).toBeTruthy();
  });
});

describe("Turning off the authenticator app", () => {
  it("turns it off, and the row offers set-up again", async () => {
    await openSecurity();
    await fireEvent.press(screen.getByTestId("on-authenticator"));
    await screen.findByText("Turn off the authenticator app?");
    await fireEvent.press(screen.getByTestId("confirm-authenticator-off"));
    await screen.findByText("Authenticator app turned off");
    await fireEvent.press(screen.getByTestId("done"));
    expect(await screen.findByTestId("setup-authenticator")).toBeTruthy();
    expect(screen.getByTestId("on-passkey")).toBeTruthy();
  });
});
