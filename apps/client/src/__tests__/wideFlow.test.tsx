import { act, fireEvent, screen, within } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

const mockSave = jest.fn(async (_name: string, _csv: string) => undefined);

jest.mock("@/theme/useIsWide", () => ({ useIsWide: () => true }));

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/investing/stubInvestingClient", () => {
  const actual = jest.requireActual("@/investing/stubInvestingClient");
  return { ...actual, createStubInvestingClient: () => actual.createStubInvestingClient({ delayMs: 0 }) };
});

jest.mock("@/activity/stubActivityClient", () => {
  const actual = jest.requireActual("@/activity/stubActivityClient");
  return { ...actual, createStubActivityClient: (phone: string | null) => actual.createStubActivityClient(phone, { delayMs: 0 }) };
});

jest.mock("@/activity/saveCsv", () => ({ saveCsv: (name: string, csv: string) => mockSave(name, csv) }));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

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

async function visit(path: string) {
  await act(async () => {
    router.push(path);
  });
}

beforeAll(warmRouter, WARM_ROUTER_MS);
beforeEach(() => mockSave.mockClear());

describe("Invest on a wide screen", () => {
  it("compares the products and works out the chosen one, then goes on to review", async () => {
    await logIn();
    await visit("/invest");
    const compare = await screen.findByTestId("compare");
    expect(within(compare).getByText("UGX 1,000,000 earns")).toBeTruthy();
    expect(within(compare).getByText("You get it back on")).toBeTruthy();
    // The 91 day bill is chosen first, and the calculator quotes UGX 1,000,000 in it.
    expect(await within(screen.getByTestId("calculator")).findByText(/^UGX 1,0\d\d,\d{3}$/)).toBeTruthy();
    expect(screen.getByTestId("calc-earn")).toHaveTextContent(/^UGX [\d,]+$/);

    await fireEvent.press(screen.getByTestId("product-TREA-182"));
    expect(within(screen.getByTestId("calculator")).getByText("182 Day Treasury Bill")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("calc-amount"), "2000000");
    expect(screen.getByTestId("calc-amount").props.value).toBe("2,000,000");
    expect(await within(screen.getByTestId("calculator")).findByText(/^UGX 2,\d{3},\d{3}$/)).toBeTruthy();

    await fireEvent.press(screen.getByTestId("calc-continue"));
    expect(await screen.findByText(/UGX 2,000,000/)).toBeTruthy();
  });
});

describe("Activity on a wide screen", () => {
  it("opens a complaint about a payment from its details", async () => {
    await logIn();
    await visit("/activity");
    await fireEvent.changeText(await screen.findByTestId("activity-search"), "bank transfer");
    expect(within(screen.getByTestId("activity-detail")).getByText("Payment for 182 Day Treasury Bill")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("detail-help"));
    expect(
      await screen.findByText("This complaint is about your payment with Finvest reference demo-activity-pay-3. We add the reference for you."),
    ).toBeTruthy();
  });

  it("shows a table with the chosen row's details, searches it, and saves what is shown as CSV", async () => {
    await logIn();
    await visit("/activity");
    const table = await screen.findByTestId("activity-table");
    expect(within(table).getByText("Payment for 182 Day Treasury Bill")).toBeTruthy();
    expect(within(table).getByText("Payout")).toBeTruthy();
    // The newest row is shown beside the table to start with.
    expect(screen.getByTestId("activity-detail")).toBeTruthy();

    await fireEvent.changeText(screen.getByTestId("activity-search"), "payout");
    expect(within(screen.getByTestId("activity-table")).queryByText("Payment for 182 Day Treasury Bill")).toBeNull();
    expect(within(screen.getByTestId("activity-detail")).getByText("Payout")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("activity-csv"));
    expect(mockSave).toHaveBeenCalledTimes(1);
    const [name, csv] = mockSave.mock.calls[0]!;
    expect(name).toMatch(/^finvest-activity-\d{4}-\d{2}-\d{2}\.csv$/);
    const lines = csv.trim().split("\r\n");
    expect(lines[0]).toBe("Date (Kampala),What,For,Direction,Amount,Currency,Status");
    expect(lines.slice(1).every((line) => line.includes(",Payout,"))).toBe(true);

    await fireEvent.changeText(screen.getByTestId("activity-search"), "no such thing");
    expect(screen.getByTestId("activity-empty")).toHaveTextContent(/Nothing matches/);
  });

  it("filters to what was paid in", async () => {
    await logIn();
    await visit("/activity");
    await screen.findByTestId("activity-table");
    await fireEvent.press(screen.getByTestId("filter-payment"));
    expect(within(screen.getByTestId("activity-table")).queryByText("Payout")).toBeNull();
    expect(within(screen.getByTestId("activity-table")).getAllByText(/^Payment for/).length).toBeGreaterThan(0);
  });
});
