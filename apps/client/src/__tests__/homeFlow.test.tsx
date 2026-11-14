import { fireEvent, screen, within } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

const mockSample = { value: "default" };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/investing/stubInvestingClient", () => {
  const actual = jest.requireActual("@/investing/stubInvestingClient");
  return {
    ...actual,
    createStubInvestingClient: () => actual.createStubInvestingClient({ delayMs: 0, sample: mockSample.value }),
  };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function logIn() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByTestId("home");
}

beforeAll(warmRouter, WARM_ROUTER_MS);
beforeEach(() => {
  mockSample.value = "default";
});

describe("Home", () => {
  it("shows what is invested, what it earns a day, the quick actions and what matures next", async () => {
    await logIn();
    const total = await screen.findByTestId("home-total");
    expect(within(total).getByText("Total invested")).toBeTruthy();
    expect(within(total).getByText(/^Earning UGX [\d,]+ a day$/)).toBeTruthy();
    for (const id of ["quick-invest", "quick-statements", "quick-withdraw", "quick-send"]) {
      expect(screen.getByTestId(id)).toBeTruthy();
    }
    // The demo saves the 182 day bill to a goal, so Investments and Goals each hold one.
    expect(screen.getByTestId("home-investments")).toHaveTextContent(/1 active, next matures/);
    expect(screen.getByTestId("home-goals")).toHaveTextContent(/2 goals, 1 saving running/);
    expect(screen.getByTestId("home-maturing-next")).toHaveTextContent(/91 Day Treasury Bill/);
    expect(screen.getByTestId("home-maturing-next")).toHaveTextContent(/Reinvest is on/);
  });

  it("lists what is left to set up, and opens a step", async () => {
    await logIn();
    const list = await screen.findByTestId("home-set-up");
    expect(within(list).getByTestId("set-up-next-of-kin")).toHaveTextContent(/Done/);
    expect(within(list).getByTestId("set-up-goal")).toHaveTextContent(/Done/);
    expect(within(list).getByTestId("set-up-questions")).toHaveTextContent(/Takes a minute/);
    await fireEvent.press(within(list).getByTestId("set-up-questions"));
    expect(await screen.findByText(/questions/i)).toBeTruthy();
  });

  it("opens Portfolio to choose an investment from Withdraw", async () => {
    await logIn();
    await fireEvent.press(await screen.findByTestId("quick-withdraw"));
    expect(await screen.findByText("Choose the investment you want to withdraw from.")).toBeTruthy();
    expect(await screen.findByTestId("portfolio-totals")).toHaveTextContent(/Invested in shillings/);
    expect(screen.getByTestId("product-split")).toHaveTextContent(/91 Day Treasury Bill, \d+%/);
  });

  it("greets a client with nothing invested with what a first investment could earn", async () => {
    mockSample.value = "empty";
    await logIn();
    expect(await screen.findByTestId("home-first-time")).toHaveTextContent(/UGX 0/);
    expect(await screen.findByTestId("home-example")).toHaveTextContent(
      /^UGX 100,000 in a 91 day bill would earn about UGX [\d,]+ by \w+, after tax\.$/,
    );
    expect(screen.queryByTestId("quick-invest")).toBeNull();
    expect(await screen.findByTestId("set-up-goal")).toHaveTextContent(/School fees, land, an emergency fund/);
  });
});
