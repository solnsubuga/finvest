import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CODE, DEMO_PASSWORD } from "@/auth/stubAuthClient";
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

const NEW_PASSWORD = "posho and beans tuesday";

async function openReset(phone: string) {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Log in"));
  await fireEvent.press(await screen.findByText("Forgot password?"));
  await screen.findByText("Reset your password");
  await fireEvent.changeText(screen.getByTestId("phone-input"), phone);
  await fireEvent.press(screen.getByTestId("reset-submit"));
  await screen.findByText("Choose a new password");
}

async function fill(code: string, password: string) {
  await fireEvent.changeText(screen.getByTestId("code-input"), code);
  await fireEvent.changeText(screen.getByTestId("password-input"), password);
  await fireEvent.changeText(screen.getByTestId("password-confirm-input"), password);
  await fireEvent.press(screen.getByTestId("reset-complete"));
}

async function logIn(password: string) {
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), password);
  await fireEvent.press(screen.getByTestId("login-submit"));
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("Forgot password", () => {
  it("sets a new password with the texted code, and the old one stops working", async () => {
    await openReset("772123456");
    await fill(DEMO_CODE, NEW_PASSWORD);
    await screen.findByText("Your password is changed");
    expect(screen.getByText(/We signed you out on every device/)).toBeTruthy();

    await fireEvent.press(screen.getByTestId("done"));
    await screen.findByText("Welcome back");
    await logIn(DEMO_PASSWORD);
    await screen.findByText(/That number and password do not match/);

    await logIn(NEW_PASSWORD);
    await screen.findByText("Confirm it is you");
  });

  it("keeps the new password typed in and asks again for a wrong code", async () => {
    await openReset("772123456");
    await fill("000000", NEW_PASSWORD);
    await screen.findByText("That code is not right. Check it and try again.");
    expect(screen.getByText("Choose a new password")).toBeTruthy();

    await fireEvent.changeText(screen.getByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("reset-complete"));
    await screen.findByText("Your password is changed");
  });

  it("says the same about a number with no account, and refuses its code", async () => {
    await openReset("700000999");
    expect(screen.getByText(/has a Finvest account, we have texted it a 6 digit code/)).toBeTruthy();
    await fill(DEMO_CODE, NEW_PASSWORD);
    await screen.findByText("That code is not right. Check it and try again.");
  });

  it("sends the code again, and the new code works", async () => {
    await openReset("772123456");
    await fireEvent.press(screen.getByTestId("reset-resend"));
    await screen.findByText("We sent a new code. Only the newest one works.");
    await fill(DEMO_CODE, NEW_PASSWORD);
    await screen.findByText("Your password is changed");
  });

  it("holds the button until the password meets the rules", async () => {
    await openReset("772123456");
    await fireEvent.changeText(screen.getByTestId("code-input"), DEMO_CODE);
    await fireEvent.changeText(screen.getByTestId("password-input"), "short");
    await fireEvent.changeText(screen.getByTestId("password-confirm-input"), "short");
    expect(screen.getByTestId("reset-complete")).toBeDisabled();
  });
});
