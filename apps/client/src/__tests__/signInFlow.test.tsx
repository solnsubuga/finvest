import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CODE, DEMO_PASSWORD } from "@/auth/stubAuthClient";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }),
  };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function openApp() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
}

async function press(label: string) {
  await fireEvent.press(screen.getByText(label));
}

describe("the shell, from the first screen", () => {
  it("sends a signed-out visitor to the welcome screen", async () => {
    await openApp();
    expect(screen.getByText("Create an account")).toBeTruthy();
    expect(screen.getByText("Log in")).toBeTruthy();
    // The stub is answering, and the screen says so.
    expect(screen.getByText(/Demo sign-in, not connected to Finvest yet/)).toBeTruthy();
  });

  it("logs in with the password and then a passkey", async () => {
    await openApp();
    await press("Log in");
    await screen.findByText("Welcome back");

    await fireEvent.changeText(screen.getByTestId("phone-input"), "0772123456");
    await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
    await fireEvent.press(screen.getByTestId("login-submit"));

    // The password alone is not enough: the second step comes first.
    await screen.findByText("Confirm it is you");
    expect(screen.queryByText("Welcome, Amara")).toBeNull();

    await fireEvent.press(screen.getByTestId("use-passkey"));
    await screen.findByText("Welcome, Amara");
  });

  it("shows one message for a wrong password and stays on the form", async () => {
    await openApp();
    await press("Log in");
    await screen.findByText("Welcome back");

    await fireEvent.changeText(screen.getByTestId("phone-input"), "772123456");
    await fireEvent.changeText(screen.getByTestId("password-input"), "not the password");
    await fireEvent.press(screen.getByTestId("login-submit"));

    await screen.findByText("That number and password do not match. Check both and try again.");
    expect(screen.getByText("Welcome back")).toBeTruthy();
  });

  it("warns about the 24 hour limit after logging in with a code", async () => {
    await openApp();
    await press("Log in");
    await screen.findByText("Welcome back");
    await fireEvent.changeText(screen.getByTestId("phone-input"), "772123456");
    await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
    await fireEvent.press(screen.getByTestId("login-submit"));
    await screen.findByText("Confirm it is you");
    await fireEvent.press(screen.getByTestId("send-code"));
    await screen.findByText("Enter the code we sent you");
    await fireEvent.changeText(screen.getByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));
    await screen.findByText(/You signed in with a code/);
  });

  it("signs a new client up and lands on the home screen", async () => {
    await openApp();
    await press("Create an account");
    await screen.findByText("Create your account");

    // Nothing is sent until the form is complete.
    await fireEvent.press(screen.getByTestId("signup-submit"));
    expect(screen.getByText("Enter a mobile number like 772 123 456.")).toBeTruthy();

    await fireEvent.changeText(screen.getByTestId("phone-input"), "701555012");
    await fireEvent.changeText(screen.getByTestId("email-input"), "new@example.com");
    await fireEvent.press(screen.getByTestId("terms-checkbox"));
    await fireEvent.press(screen.getByTestId("signup-submit"));

    await screen.findByText("Enter the code we sent you");
    await fireEvent.changeText(screen.getByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("code-submit"));

    await screen.findByText("Create a password");
    await fireEvent.changeText(screen.getByTestId("password-input"), "green matooke sunday");
    await fireEvent.changeText(screen.getByTestId("password-confirm-input"), "green matooke sunday");
    await fireEvent.press(screen.getByTestId("password-submit"));

    await waitFor(() => expect(screen.getByText("Welcome to Finvest")).toBeTruthy());
  });
});
