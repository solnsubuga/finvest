import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CODE } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

const links: string[] = [];
const held: { client: { confirmEmail(token: string): Promise<void> } | null } = { client: null };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () => {
      const client = actual.createStubAuthClient({ delayMs: 0, onEmailLink: (token: string) => links.push(token) });
      held.client = client;
      return client;
    },
  };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function signUpTo(step: string) {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Create an account"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772 900 411");
  await fireEvent.changeText(screen.getByTestId("email-input"), "amara@example.com");
  await fireEvent.press(screen.getByTestId("terms-checkbox"));
  await fireEvent.press(screen.getByTestId("signup-submit"));
  await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
  await fireEvent.press(screen.getByTestId("code-submit"));
  await fireEvent.changeText(await screen.findByTestId("password-input"), "green matooke sunday");
  await fireEvent.changeText(screen.getByTestId("password-confirm-input"), "green matooke sunday");
  await fireEvent.press(screen.getByTestId("password-submit"));
  await screen.findByText(step);
}

beforeAll(warmRouter, WARM_ROUTER_MS);
beforeEach(() => {
  links.length = 0;
});

describe("confirming the email at sign up", () => {
  it("emails a link, says so, and lets the client carry on without it", async () => {
    await signUpTo("Check your email");
    expect(links).toHaveLength(1);
    expect(screen.getByText("amara@example.com")).toBeTruthy();
    expect(screen.getByText(/You can start using Finvest now\./)).toBeTruthy();

    await fireEvent.press(screen.getByTestId("email-step-resend"));
    expect(await screen.findByText(/We emailed a new link to amara@example.com/)).toBeTruthy();
    expect(links).toHaveLength(2);

    await fireEvent.press(screen.getByTestId("email-step-later"));
    await screen.findByTestId("home");
    // Home keeps asking until the address is confirmed.
    expect(await screen.findByTestId("email-proof-banner")).toBeTruthy();
    expect(screen.getByText("Confirm your email")).toBeTruthy();
  });

  it("stops asking once the link is opened", async () => {
    await signUpTo("Check your email");
    await fireEvent.press(screen.getByTestId("email-step-later"));
    await screen.findByTestId("email-proof-banner");

    // The client opens the newest link, in another tab or on another device.
    await held.client?.confirmEmail(links[links.length - 1] as string);
    // Coming back to Home reads the session again.
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(screen.getByLabelText("Home"));
    await waitFor(() => expect(screen.queryByTestId("email-proof-banner")).toBeNull());
  });
});
