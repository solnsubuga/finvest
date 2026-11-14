import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** The link tokens the stub "emailed", and the URL the phone was opened with. */
const mockMail: { tokens: string[]; url: string | null } = { tokens: [], url: null };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () =>
      actual.createStubAuthClient({ delayMs: 0, onEmailLink: (token: string) => mockMail.tokens.push(token) }),
  };
});

jest.mock("@/auth/emailLink", () => {
  const actual = jest.requireActual("@/auth/emailLink");
  return { ...actual, useLinkToken: () => actual.tokenFromLink(mockMail.url) };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

beforeEach(() => {
  mockMail.tokens = [];
  mockMail.url = null;
});

async function logInAndOpenProfile(phone: string) {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), phone);
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await screen.findByText("Confirm it is you");
}

async function openLink(token: string) {
  mockMail.url = `finvest://verify-email#${token}`;
  await act(async () => {
    router.push("/verify-email");
  });
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("Proving an email address", () => {
  it("emails a link, and opening it marks the address proved on Profile", async () => {
    await logInAndOpenProfile("772123456");
    await fireEvent.press(screen.getByTestId("use-passkey"));
    await screen.findByText("Welcome, Amara");
    await fireEvent.press(screen.getByLabelText("Profile"));
    expect(await screen.findByText("Not proved yet")).toBeTruthy();
    expect(screen.getByText("amara@example.com")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("send-email-proof"));
    await screen.findByText(/We emailed a link to amara@example.com/);
    expect(mockMail.tokens).toHaveLength(1);

    await openLink(mockMail.tokens[0]!);
    await screen.findByText("Your email address is proved");
    await fireEvent.press(screen.getByTestId("done"));
    expect(await screen.findByText("Proved")).toBeTruthy();
    expect(screen.queryByTestId("send-email-proof")).toBeNull();
  });

  it("refuses an old link once a newer one was sent", async () => {
    await logInAndOpenProfile("772123456");
    await fireEvent.press(screen.getByTestId("use-passkey"));
    await fireEvent.press(await screen.findByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("send-email-proof"));
    await screen.findByText("Send another link");
    await fireEvent.press(screen.getByTestId("send-email-proof"));
    await screen.findByText(/We emailed a link/);
    expect(mockMail.tokens).toHaveLength(2);

    await openLink(mockMail.tokens[0]!);
    await screen.findByText("This link no longer works");
  });

  it("says nothing needs proving for an address that already is", async () => {
    await logInAndOpenProfile("701000200");
    await fireEvent.press(screen.getByTestId("use-authenticator"));
    await fireEvent.changeText(await screen.findByTestId("code-input"), "123456");
    await fireEvent.press(screen.getByTestId("code-submit"));
    await fireEvent.press(await screen.findByLabelText("Profile"));
    expect(await screen.findByText("Proved")).toBeTruthy();
    expect(screen.getByText("Statements and receipts are emailed here.")).toBeTruthy();
  });
});
