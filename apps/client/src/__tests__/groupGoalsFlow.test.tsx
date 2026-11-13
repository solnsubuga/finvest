import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/payments/stubPaymentsClient", () => {
  const actual = jest.requireActual("@/payments/stubPaymentsClient");
  return { ...actual, createStubPaymentsClient: () => actual.createStubPaymentsClient({ delayMs: 0 }) };
});

const NOW = () => Date.parse("2026-11-13T09:00:00Z");

jest.mock("@/goals/stubGoalsClient", () => {
  const actual = jest.requireActual("@/goals/stubGoalsClient");
  return {
    ...actual,
    createStubGoalsClient: (options: object) =>
      actual.createStubGoalsClient({ ...options, delayMs: 0, now: () => Date.parse("2026-11-13T09:00:00Z") }),
  };
});

jest.mock("@/goals/stubGroupsClient", () => {
  const actual = jest.requireActual("@/goals/stubGroupsClient");
  return {
    ...actual,
    createStubGroupsClient: (options: object) =>
      actual.createStubGroupsClient({ ...options, delayMs: 0, now: () => Date.parse("2026-11-13T09:00:00Z") }),
  };
});

jest.mock("@/goals/amount", () => ({
  ...jest.requireActual("@/goals/amount"),
  kampalaToday: () => "2026-11-13",
}));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("expo-clipboard", () => ({ setStringAsync: jest.fn(async () => true) }));

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

async function openGoals() {
  await signIn("772123456");
  await screen.findByText("Welcome, Amara");
  await fireEvent.press(screen.getByLabelText("Goals"));
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("group goals", () => {
  it("shows the demo family group once, not the own part as a goal of its own", async () => {
    expect(NOW()).toBeGreaterThan(0);
    await openGoals();
    expect(await screen.findByText("Family land in Mukono")).toBeTruthy();
    expect(screen.getAllByText("Family land in Mukono")).toHaveLength(1);
    expect(screen.getByText("You, Daniel and Miriam")).toBeTruthy();
  });

  it("opens a group with names and the combined total, and makes a link to share", async () => {
    await openGoals();
    await fireEvent.press(await screen.findByText("Family land in Mukono"));
    expect(await screen.findByText("Saved together")).toBeTruthy();
    expect(screen.getByText("UGX 2,800,000")).toBeTruthy();
    expect(screen.getByText("Members, 3 of 10")).toBeTruthy();
    expect(screen.getByText("Daniel")).toBeTruthy();
    // Nothing a member saved on their own is ever shown.
    expect(screen.queryByText("UGX 1,850,000")).toBeNull();

    await fireEvent.press(screen.getByTestId("group-make-link"));
    const link = await screen.findByTestId("group-link");
    expect(String(link.props.children)).toMatch(/^https:\/\/app\.finvestug\.com\/goals\/groups\/join#[A-Za-z0-9_-]{22}$/);
  });

  it("checks the set-up form, then sets a group up and opens it", async () => {
    await openGoals();
    await fireEvent.press(await screen.findByTestId("new-group"));
    await fireEvent.press(await screen.findByTestId("group-create"));
    expect(await screen.findByText("Choose what the group is saving for.")).toBeTruthy();
    expect(screen.getByText("Enter the name the others will see.")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("group-category-wedding"));
    await fireEvent.changeText(screen.getByTestId("group-name"), "Our wedding");
    await fireEvent.changeText(screen.getByTestId("group-target"), "10000000");
    await fireEvent.changeText(screen.getByTestId("group-part"), "20000000");
    await fireEvent.changeText(screen.getByTestId("group-you"), "Amara");
    await fireEvent.press(screen.getByTestId("group-create"));
    expect(await screen.findByText("Your part cannot be more than the group's target.")).toBeTruthy();

    await fireEvent.changeText(screen.getByTestId("group-part"), "5000000");
    await fireEvent.press(screen.getByTestId("group-create"));
    expect(await screen.findByText("Our wedding")).toBeTruthy();
    expect(screen.getByText("Members, 1 of 10")).toBeTruthy();
    expect(screen.getByText("Only your part so far. Invite others to save with you.")).toBeTruthy();
  });

  it("joins with a code after showing what the others will see", async () => {
    await openGoals();
    await fireEvent.press(await screen.findByTestId("join-group"));
    await fireEvent.changeText(await screen.findByTestId("join-code"), "not a code");
    await fireEvent.press(screen.getByTestId("group-look-up"));
    expect(await screen.findByText("Paste the whole link, or the 22-character code from it.")).toBeTruthy();

    await fireEvent.changeText(screen.getByTestId("join-code"), "ZZZZZZZZZZZZZZZZZZZZZZ");
    await fireEvent.press(screen.getByTestId("group-look-up"));
    expect(await screen.findByText(/This link is not working/)).toBeTruthy();

    await fireEvent.changeText(
      screen.getByTestId("join-code"),
      "https://app.finvestug.com/goals/groups/join#DemoInviteWeddingMbara",
    );
    await fireEvent.press(screen.getByTestId("group-look-up"));
    expect(await screen.findByText("Wedding in Mbarara")).toBeTruthy();
    expect(screen.getByText(/never what you save on its own/)).toBeTruthy();

    await fireEvent.changeText(screen.getByTestId("join-part"), "2000000");
    await fireEvent.changeText(screen.getByTestId("join-you"), "Amara");
    await fireEvent.press(screen.getByTestId("group-join"));
    expect(await screen.findByText("Saved together")).toBeTruthy();
    expect(screen.getByText("Members, 4 of 10")).toBeTruthy();
    expect(screen.getByText("Leave this group goal")).toBeTruthy();
  });

  it("leaves a group and keeps the own goal", async () => {
    await openGoals();
    await fireEvent.press(await screen.findByTestId("join-group"));
    await fireEvent.changeText(await screen.findByTestId("join-code"), "DemoInviteWeddingMbara");
    await fireEvent.press(screen.getByTestId("group-look-up"));
    await fireEvent.changeText(await screen.findByTestId("join-part"), "2000000");
    await fireEvent.changeText(screen.getByTestId("join-you"), "Amara");
    await fireEvent.press(screen.getByTestId("group-join"));
    await fireEvent.press(await screen.findByText("Leave this group goal"));
    await fireEvent.press(await screen.findByTestId("group-end-yes"));
    // Back on the list, the part is now an ordinary goal of the client's own.
    expect(await screen.findByText("Wedding in Mbarara")).toBeTruthy();
    expect(screen.queryByTestId("group-demo-group-wedding")).toBeNull();
  });
});
