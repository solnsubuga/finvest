import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { AutoinvestError } from "@/autoinvest/types";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }),
  };
});

const mockCreate = jest.fn();

// expo-router's test renderer installs fake timers, so the demo answers at once.
jest.mock("@/autoinvest/stubAutoinvestClient", () => {
  const actual = jest.requireActual("@/autoinvest/stubAutoinvestClient");
  return {
    ...actual,
    createStubAutoinvestClient: (phone: string | null) => {
      const client = actual.createStubAutoinvestClient(phone, actual.newStubStore(), undefined, 0);
      return {
        ...client,
        createPlan: async (request: unknown) => {
          mockCreate(request);
          return client.createPlan(request);
        },
      };
    },
  };
});

// The preset's mock of the native module returns nothing. Tests need real, distinct keys.
jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

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

jest.setTimeout(30_000);

it("starts a plan, shows when it runs and pauses it", async () => {
  await signIn();
  await fireEvent.press(screen.getByTestId("home-autoinvest"));
  // The demo account already has one plan.
  await screen.findByText("UGX 250,000");
  await fireEvent.press(screen.getByTestId("plan-new"));
  await screen.findByText("New plan");

  // Below the product's minimum: refused on the form, nothing is sent.
  await fireEvent.changeText(await screen.findByTestId("plan-amount"), "50,000");
  await fireEvent.press(screen.getByTestId("plan-start"));
  await screen.findByText("The 91 Day Treasury Bill takes at least UGX 100,000.");

  await fireEvent.changeText(screen.getByTestId("plan-amount"), "300,000");
  await fireEvent.press(screen.getByTestId("frequency-weekly"));
  await fireEvent.press(screen.getByTestId("weekday-5"));
  expect(screen.getByTestId("plan-summary")).toHaveTextContent(/Every Friday, from/);
  await fireEvent.press(screen.getByTestId("plan-start"));

  expect(await screen.findByLabelText("Each time: UGX 300,000")).toBeTruthy();
  expect(screen.getByLabelText("When: Every Friday")).toBeTruthy();
  expect(await screen.findByTestId("plan-next")).toHaveTextContent(/Next prompt/);
  await fireEvent.press(screen.getByTestId("plan-pause"));
  expect(await screen.findByTestId("plan-resume")).toBeTruthy();
  expect(screen.getByTestId("plan-next")).toHaveTextContent(/Paused/);
});

it("after a lost answer, fixes the plan and sends the same one again", async () => {
  mockCreate.mockClear();
  await signIn();
  await fireEvent.press(screen.getByTestId("home-autoinvest"));
  await screen.findByText("UGX 250,000");
  await fireEvent.press(screen.getByTestId("plan-new"));
  await fireEvent.changeText(await screen.findByTestId("plan-amount"), "300,000");
  mockCreate.mockImplementationOnce(() => {
    throw new AutoinvestError("unavailable");
  });
  await fireEvent.press(screen.getByTestId("plan-start"));
  await screen.findByText(/Your plan may already have reached us/);

  // Nothing on the form can change now.
  expect(screen.getByTestId("plan-amount").props.editable).toBe(false);
  await fireEvent.press(screen.getByTestId("frequency-weekly"));
  expect(screen.getByTestId("frequency-monthly")).toBeChecked();
  expect(screen.getByTestId("day-5")).toBeDisabled();

  await fireEvent.press(screen.getByTestId("plan-start"));
  expect(await screen.findByLabelText("Each time: UGX 300,000")).toBeTruthy();
  expect(mockCreate).toHaveBeenCalledTimes(2);
  expect(mockCreate.mock.calls[1][0]).toEqual(mockCreate.mock.calls[0][0]);
});

it("leaves the form open after a refusal, because nothing was saved", async () => {
  await signIn();
  await fireEvent.press(screen.getByTestId("home-autoinvest"));
  await screen.findByText("UGX 250,000");
  await fireEvent.press(screen.getByTestId("plan-new"));
  await fireEvent.changeText(await screen.findByTestId("plan-amount"), "300,000");
  mockCreate.mockImplementationOnce(() => {
    throw new AutoinvestError("not_verified");
  });
  await fireEvent.press(screen.getByTestId("plan-start"));
  await screen.findByText(/once your identity check is approved/);
  expect(screen.queryByText(/Your plan may already have reached us/)).toBeNull();
  await fireEvent.press(screen.getByTestId("frequency-weekly"));
  expect(screen.getByTestId("frequency-weekly")).toBeChecked();
});

it("shows insights after tax for the demo account", async () => {
  await signIn();
  await fireEvent.press(screen.getByTestId("home-insights"));
  expect(await screen.findByTestId("insights-chart")).toHaveTextContent(/after tax/);
  expect(screen.getByTestId("insights-next")).toHaveTextContent(/matures in 57 days/);
});
