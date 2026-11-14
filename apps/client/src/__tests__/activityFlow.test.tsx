import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CODE, DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/activity/stubActivityClient", () => {
  const actual = jest.requireActual("@/activity/stubActivityClient");
  return { ...actual, createStubActivityClient: (phone: string | null) => actual.createStubActivityClient(phone, { delayMs: 0 }) };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function signUp(local: string) {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Create an account"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), local);
  await fireEvent.changeText(screen.getByTestId("email-input"), "new@example.com");
  await fireEvent.press(screen.getByTestId("terms-checkbox"));
  await fireEvent.press(screen.getByTestId("signup-submit"));
  await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
  await fireEvent.press(screen.getByTestId("code-submit"));
  await fireEvent.changeText(await screen.findByTestId("password-input"), "green matooke sunday");
  await fireEvent.changeText(screen.getByTestId("password-confirm-input"), "green matooke sunday");
  await fireEvent.press(screen.getByTestId("password-submit"));
  await screen.findByText("Welcome to Finvest");
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("Activity", () => {
  it("lists payments, interest and payouts newest first, and filters them", async () => {
    await renderRouter("./src/app", { initialUrl: "/" });
    await screen.findByText("Your money, working every day.");
    await fireEvent.press(screen.getByText("Log in"));
    await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
    await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
    await fireEvent.press(screen.getByTestId("login-submit"));
    await fireEvent.press(await screen.findByTestId("use-passkey"));
    await screen.findByText("Welcome, Amara");
    await fireEvent.press(screen.getByLabelText("Activity"));

    expect(await screen.findByText("Paid for 182 day Treasury bill")).toBeTruthy();
    expect(screen.getByText("Being checked")).toBeTruthy();
    expect(screen.getByText("Did not go through")).toBeTruthy();
    expect(screen.getByText("Interest on 91 day Treasury bill")).toBeTruthy();
    expect(screen.getByText("+UGX 15,400")).toBeTruthy();
    expect(screen.getByText("Payout")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("filter-interest"));
    expect(screen.queryByText("Payout")).toBeNull();
    expect(screen.getByText("Interest on 91 day Treasury bill")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("filter-payout"));
    expect(screen.getByText("Paid to you")).toBeTruthy();
    expect(screen.queryByText(/^Paid for/)).toBeNull();
  });

  it("says nothing has happened yet for a new client", async () => {
    await signUp("701555012");
    await fireEvent.press(screen.getByLabelText("Activity"));
    expect(await screen.findByText(/Nothing here yet. Your payments, interest and payouts/)).toBeTruthy();
  });

  it("offers to try again when the list cannot be read", async () => {
    await signUp("701555099");
    await fireEvent.press(screen.getByLabelText("Activity"));
    expect(await screen.findByText(/We could not load your activity/)).toBeTruthy();
    expect(screen.getByTestId("activity-retry")).toBeTruthy();
  });
});
