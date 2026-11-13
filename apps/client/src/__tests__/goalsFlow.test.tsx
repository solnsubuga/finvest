import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/payments/stubPaymentsClient", () => {
  const actual = jest.requireActual("@/payments/stubPaymentsClient");
  return { ...actual, createStubPaymentsClient: () => actual.createStubPaymentsClient({ delayMs: 0 }) };
});

// When set, the next saving is made but its answer is lost on the way back.
let mockLoseNextSavingAnswer = false;

jest.mock("@/goals/stubGoalsClient", () => {
  const actual = jest.requireActual("@/goals/stubGoalsClient");
  return {
    ...actual,
    createStubGoalsClient: (options: object) => {
      const client = actual.createStubGoalsClient({
        ...options,
        delayMs: 0,
        now: () => Date.parse("2026-11-13T09:00:00Z"),
      });
      return {
        ...client,
        async saveToGoal(...args: unknown[]) {
          const saving = await client.saveToGoal(...args);
          if (mockLoseNextSavingAnswer) {
            mockLoseNextSavingAnswer = false;
            throw new TypeError("Network request failed");
          }
          return saving;
        },
      };
    },
  };
});

jest.mock("@/goals/amount", () => ({
  ...jest.requireActual("@/goals/amount"),
  kampalaToday: () => "2026-11-13",
}));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function signIn(phone: string) {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), phone);
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId(phone === "772123456" ? "use-passkey" : "use-authenticator"));
}

// The first test in a file renders the whole router from cold, which takes several seconds on
// a busy CI runner. The assertions are unchanged; only the time allowed to reach them is.
jest.setTimeout(30_000);

describe("goals", () => {
  it("lists the demo account's goals with their progress", async () => {
    await signIn("772123456");
    await screen.findByText("Welcome, Amara");
    await fireEvent.press(screen.getByLabelText("Goals"));
    expect(await screen.findByText("School fees, Term 1")).toBeTruthy();
    expect(screen.getByText("Emergency fund")).toBeTruthy();
  });

  it("sets up a goal in three steps, then pays the first saving", async () => {
    await signIn("772123456");
    await screen.findByText("Welcome, Amara");
    await fireEvent.press(screen.getByLabelText("Goals"));
    await fireEvent.press(await screen.findByTestId("new-goal"));

    await fireEvent.press(await screen.findByTestId("goal-continue"));
    expect(await screen.findByText("Choose what you are saving for.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("category-land_or_home"));
    await fireEvent.changeText(screen.getByTestId("goal-name"), "Plot in Wakiso");
    await fireEvent.press(screen.getByTestId("goal-continue"));

    await fireEvent.changeText(await screen.findByTestId("goal-target"), "2000000");
    expect(await screen.findByText("UGX 189,000")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("plan-next"));

    expect(await screen.findByText("How do you want to start?")).toBeTruthy();
    expect(screen.getByTestId("first-saving").props.value).toBe("189,000");
    await fireEvent.press(screen.getByTestId("create-and-save"));
    expect(await screen.findByText("Pay UGX 189,000")).toBeTruthy();
    expect(screen.getByText("Plot in Wakiso: 91 Day Treasury Bill")).toBeTruthy();
  });

  it("refuses a first saving below the minimum without creating anything", async () => {
    await signIn("772123456");
    await screen.findByText("Welcome, Amara");
    await fireEvent.press(screen.getByLabelText("Goals"));
    await fireEvent.press(await screen.findByTestId("new-goal"));
    await fireEvent.press(await screen.findByTestId("category-emergency"));
    await fireEvent.press(screen.getByTestId("goal-continue"));
    await fireEvent.changeText(await screen.findByTestId("goal-target"), "3000000");
    await fireEvent.press(screen.getByTestId("horizon-none"));
    await fireEvent.press(screen.getByTestId("plan-next"));
    await fireEvent.changeText(await screen.findByTestId("first-saving"), "50000");
    await fireEvent.press(screen.getByTestId("create-and-save"));
    expect(await screen.findByText("Each saving is at least UGX 100,000.")).toBeTruthy();
  });

  it("fixes the first saving after a lost answer and sends the same one again", async () => {
    await signIn("772123456");
    await screen.findByText("Welcome, Amara");
    await fireEvent.press(screen.getByLabelText("Goals"));
    await fireEvent.press(await screen.findByTestId("new-goal"));
    await fireEvent.press(await screen.findByTestId("category-land_or_home"));
    await fireEvent.changeText(screen.getByTestId("goal-name"), "Plot in Wakiso");
    await fireEvent.press(screen.getByTestId("goal-continue"));
    await fireEvent.changeText(await screen.findByTestId("goal-target"), "2000000");
    await screen.findByText("UGX 189,000");
    await fireEvent.press(screen.getByTestId("plan-next"));

    mockLoseNextSavingAnswer = true;
    await fireEvent.press(await screen.findByTestId("create-and-save"));
    expect(await screen.findByText(/so the amount is\s+fixed/)).toBeTruthy();
    expect(screen.getByTestId("first-saving").props.editable).toBe(false);

    await fireEvent.press(screen.getByTestId("create-and-save"));
    expect(await screen.findByText("Pay UGX 189,000")).toBeTruthy();
  });

  it("fixes a saving to a goal after a lost answer and sends the same one again", async () => {
    await signIn("772123456");
    await screen.findByText("Welcome, Amara");
    await fireEvent.press(screen.getByLabelText("Goals"));
    await fireEvent.press(await screen.findByText("Emergency fund"));
    await fireEvent.press(await screen.findByTestId("save-now"));
    await fireEvent.changeText(await screen.findByTestId("save-amount"), "250000");

    mockLoseNextSavingAnswer = true;
    await fireEvent.press(screen.getByTestId("save-continue"));
    expect(await screen.findByText(/so its amount is fixed/)).toBeTruthy();
    expect(screen.getByTestId("save-amount").props.editable).toBe(false);

    await fireEvent.press(screen.getByTestId("save-continue"));
    expect(await screen.findByText("Pay UGX 250,000")).toBeTruthy();
  });
});
