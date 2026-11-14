import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

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

async function openRecovery() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Log in"));
  await fireEvent.press(await screen.findByText("I changed my number or cannot get in"));
  await screen.findByText("Tell us what happened", { exact: false });
}

async function askForCall(account: string, contact: string) {
  await fireEvent.changeText(screen.getByTestId("account-phone-input"), account);
  await fireEvent.changeText(screen.getByTestId("contact-phone-input"), contact);
  await fireEvent.press(screen.getByTestId("call-back-submit"));
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("Getting back in when locked out", () => {
  it("asks for a call back after a lost phone, the same way for any number", async () => {
    await openRecovery();
    await fireEvent.press(screen.getByTestId("recover-phone"));
    await screen.findByText("Ask us to call you", { exact: true });
    expect(screen.getByTestId("reason-lost_phone")).toBeChecked();

    await fireEvent.press(screen.getByTestId("call-back-submit"));
    expect(screen.getByText("Enter the number on your account, like 772 123 456.")).toBeTruthy();
    expect(screen.getByText("Enter a number we can call you on now.")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("reason-new_number"));
    expect(screen.getByTestId("reason-new_number")).toBeChecked();
    await askForCall("772123456", "701555013");
    await screen.findByText("We will call you");
    expect(screen.getByText(/If that number has a Finvest account/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("done"));
    await screen.findByText("Welcome back");
  });

  it("refuses a third ask for one number in a short time", async () => {
    await openRecovery();
    for (let ask = 0; ask < 2; ask += 1) {
      await fireEvent.press(screen.getByTestId("recover-codes"));
      await askForCall("700000001", "701555013");
      await screen.findByText("We will call you");
      await fireEvent.press(screen.getByTestId("done"));
      await fireEvent.press(await screen.findByText("I changed my number or cannot get in"));
    }
    await fireEvent.press(await screen.findByTestId("recover-codes"));
    await askForCall("700000001", "701555013");
    await screen.findByText("Too many tries. Wait a few minutes and try again.");
  });

  it("leads to the email log in and the password reset", async () => {
    await openRecovery();
    await fireEvent.press(screen.getByTestId("recover-email"));
    await screen.findByText("Sign in with your email address and password.");
    expect(screen.getByTestId("email-input")).toBeTruthy();

    await fireEvent.press(screen.getByText("I changed my number or cannot get in"));
    await fireEvent.press(await screen.findByTestId("recover-password"));
    await screen.findByText("Reset your password");
  });
});
