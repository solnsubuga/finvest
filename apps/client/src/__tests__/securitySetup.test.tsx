import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CODE } from "@/auth/stubAuthClient";
import { groupKey } from "@/app/security/authenticator";
import { qrPath } from "@/components/QrCode";
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

beforeEach(() => {
  mockClock.minutes = 0;
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

const NEW_PASSWORD = "green matooke sunday";

/** Signs up a new client, who starts with SMS codes only, and lands on Home. */
async function signUp() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Create an account"));
  await screen.findByTestId("signup-submit");
  await fireEvent.changeText(screen.getByTestId("phone-input"), "701555012");
  await fireEvent.changeText(screen.getByTestId("email-input"), "new@example.com");
  await fireEvent.press(screen.getByTestId("terms-checkbox"));
  await fireEvent.press(screen.getByTestId("signup-submit"));
  await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
  await fireEvent.press(screen.getByTestId("code-submit"));
  await fireEvent.changeText(await screen.findByTestId("password-input"), NEW_PASSWORD);
  await fireEvent.changeText(screen.getByTestId("password-confirm-input"), NEW_PASSWORD);
  await fireEvent.press(screen.getByTestId("password-submit"));
  await screen.findByText("Welcome to Finvest");
}

async function logOutAndStartLogIn(password: string) {
  await fireEvent.press(screen.getByLabelText("Back"));
  await fireEvent.press(await screen.findByTestId("sign-out"));
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "701555012");
  await fireEvent.changeText(screen.getByTestId("password-input"), password);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await screen.findByText("Confirm it is you");
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("Sign in and security", () => {
  it("nudges a client with SMS only from Home, and shows what is on and what can be added", async () => {
    await signUp();
    await fireEvent.press(await screen.findByTestId("security-prompt"));
    await screen.findByText("Sign in and security");

    expect(await screen.findByTestId("setup-passkey")).toBeTruthy();
    expect(screen.getByTestId("setup-authenticator")).toBeTruthy();
    expect(screen.getByTestId("on-code")).toBeTruthy();
    expect(screen.getByText(/With only SMS codes, every log in limits withdrawals/)).toBeTruthy();
  });

  it("adds a passkey, and the next log in offers it first", async () => {
    await signUp();
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("open-security"));
    await fireEvent.press(await screen.findByTestId("setup-passkey"));
    await screen.findByText("It cannot be stolen by a fake site");

    await fireEvent.press(screen.getByTestId("add-passkey"));
    await screen.findByText("Passkey added");
    await fireEvent.press(screen.getByTestId("done"));

    expect(await screen.findByTestId("on-passkey")).toBeTruthy();
    expect(screen.queryByTestId("setup-passkey")).toBeNull();
    // With a passkey on, the SMS-only warning is gone.
    expect(screen.queryByText(/With only SMS codes/)).toBeNull();

    await logOutAndStartLogIn(NEW_PASSWORD);
    expect(screen.getByText("Your passkey")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("use-passkey"));
    await screen.findByText("Welcome to Finvest");
    expect(screen.queryByText(/You signed in with a code/)).toBeNull();
  });

  it("sets up an authenticator app only after a right code, and the next log in accepts it", async () => {
    await signUp();
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("open-security"));
    await fireEvent.press(await screen.findByTestId("setup-authenticator"));

    // Step 1: the key, as a QR code and in groups of four to type.
    await screen.findByText("Set up an authenticator app");
    expect(await screen.findByTestId("qr-code")).toBeTruthy();
    expect(String(screen.getByTestId("setup-key").props.children)).toMatch(
      /^([A-Z2-7]{4} ){7}[A-Z2-7]{4}$/,
    );
    await fireEvent.press(screen.getByTestId("key-continue"));

    // Step 2: a wrong code switches nothing on.
    await screen.findByText("Enter the code from your app");
    await fireEvent.changeText(screen.getByTestId("code-input"), "000000");
    await fireEvent.press(screen.getByTestId("code-submit"));
    await screen.findByText("That code is not right. Check it and try again.");
    expect(screen.queryByText("Authenticator app is on")).toBeNull();

    await fireEvent.changeText(screen.getByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));
    await screen.findByText("Authenticator app is on");
    await fireEvent.press(screen.getByTestId("done"));
    expect(await screen.findByTestId("on-authenticator")).toBeTruthy();

    await logOutAndStartLogIn(NEW_PASSWORD);
    await fireEvent.press(screen.getByTestId("use-authenticator"));
    await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));
    await screen.findByText("Welcome to Finvest");
  });

  it("asks for a fresh log in when the session is too old, and adds nothing", async () => {
    await signUp();
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("open-security"));
    await fireEvent.press(await screen.findByTestId("setup-passkey"));
    await screen.findByTestId("add-passkey");

    mockClock.minutes = 11;
    await fireEvent.press(screen.getByTestId("add-passkey"));
    await screen.findByText(/log in again before you change how you sign in/);
    expect(screen.queryByText("Passkey added")).toBeNull();

    expect(screen.getByText("Log in again first")).toBeTruthy();

    // Logging in again brings the client straight back to the set-up.
    await fireEvent.press(screen.getByTestId("sign-in-again"));
    await screen.findByText("Your money, working every day.");
    mockClock.minutes = 12;
    await fireEvent.press(screen.getByText("Log in"));
    await fireEvent.changeText(await screen.findByTestId("phone-input"), "701555012");
    await fireEvent.changeText(screen.getByTestId("password-input"), NEW_PASSWORD);
    await fireEvent.press(screen.getByTestId("login-submit"));
    await fireEvent.press(await screen.findByTestId("use-code"));
    await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));
    await screen.findByText("It cannot be stolen by a fake site");
    await fireEvent.press(screen.getByTestId("add-passkey"));
    await screen.findByText("Passkey added");

    // The note is spent: the next log in goes Home as usual.
    await fireEvent.press(screen.getByTestId("done"));
    await screen.findByTestId("on-passkey");
    await fireEvent.press(screen.getByLabelText("Back"));
    await screen.findByText("Welcome to Finvest");
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("sign-out"));
    await screen.findByText("Your money, working every day.");
    await fireEvent.press(screen.getByText("Log in"));
    await fireEvent.changeText(await screen.findByTestId("phone-input"), "701555012");
    await fireEvent.changeText(screen.getByTestId("password-input"), NEW_PASSWORD);
    await fireEvent.press(screen.getByTestId("login-submit"));
    await fireEvent.press(await screen.findByTestId("use-passkey"));
    await screen.findByText("Welcome to Finvest");
  });

  it("gives a new key when the set-up ran out of time, and never re-shows the old one", async () => {
    await signUp();
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("open-security"));
    await fireEvent.press(await screen.findByTestId("setup-authenticator"));
    const firstKey = String((await screen.findByTestId("setup-key")).props.children);
    await fireEvent.press(screen.getByTestId("key-continue"));

    // Six minutes: the set-up has ended, but the session is still fresh.
    mockClock.minutes = 6;
    await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));

    await screen.findByText(/the first key no longer works/);
    const secondKey = String((await screen.findByTestId("setup-key")).props.children);
    expect(secondKey).not.toBe(firstKey);
    expect(screen.queryByText("Authenticator app is on")).toBeNull();

    await fireEvent.press(screen.getByTestId("key-continue"));
    await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));
    await screen.findByText("Authenticator app is on");
  });

  it("asks for a fresh log in before showing an authenticator key to an old session", async () => {
    await signUp();
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("open-security"));
    await screen.findByTestId("setup-authenticator");

    mockClock.minutes = 11;
    await fireEvent.press(screen.getByTestId("setup-authenticator"));
    await screen.findByTestId("sign-in-again");
    expect(screen.queryByTestId("qr-code")).toBeNull();
  });

  it("sends a signed-out visitor away from the set-up screens", async () => {
    await renderRouter("./src/app", { initialUrl: "/security/authenticator" });
    await screen.findByText("Your money, working every day.");
    expect(screen.queryByText("Set up an authenticator app")).toBeNull();
  });
});

describe("the pieces", () => {
  it("groups a key in fours", () => {
    expect(groupKey("ABCDEFGH2345")).toBe("ABCD EFGH 2345");
    expect(groupKey("ABCDE")).toBe("ABCD E");
  });

  it("draws a square QR code with the three corner markers", () => {
    const { path, count } = qrPath("otpauth://totp/Finvest?secret=ABCDEFGH&issuer=Finvest");
    expect(count).toBeGreaterThanOrEqual(21);
    // The top-left, top-right and bottom-left corners are always dark.
    expect(path).toContain("M0 0h1v1h-1z");
    expect(path).toContain(`M${count - 1} 0h1v1h-1z`);
    expect(path).toContain(`M0 ${count - 1}h1v1h-1z`);
  });
});
