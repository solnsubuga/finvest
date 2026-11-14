import { fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import ChangeEmail from "@/app/security/email";
import ChangePassword from "@/app/security/password";
import ChangePhone from "@/app/security/phone";

import { AuthProvider } from "../AuthProvider";
import { createStubAuthClient, DEMO_CODE, DEMO_PASSWORD, DEMO_PHONE } from "../stubAuthClient";
import { AuthClient } from "../types";

jest.mock("expo-router", () => {
  const { useEffect } = jest.requireActual("react");
  return {
    ...jest.requireActual("expo-router"),
    useRouter: () => ({ push: jest.fn(), navigate: jest.fn(), back: jest.fn(), dismissTo: jest.fn(), canGoBack: () => false }),
    useFocusEffect: (effect: () => void | (() => void)) => useEffect(effect, [effect]),
    Stack: { Screen: () => null },
  };
});

/** The demo client, logged in with an SMS code, so a change first asks for the app. */
async function loggedInWithCode(): Promise<AuthClient> {
  const client = createStubAuthClient({ delayMs: 0 });
  const challenge = await client.startLogin({ phone: DEMO_PHONE }, DEMO_PASSWORD);
  await client.verifyWithCode(challenge.loginId, DEMO_CODE);
  return client;
}

async function show(client: AuthClient, Screen: () => React.ReactElement | null) {
  await render(
    <SafeAreaProvider
      initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}
    >
      <AuthProvider client={client}>
        <Screen />
      </AuthProvider>
    </SafeAreaProvider>,
  );
}

async function confirmWithTheApp() {
  expect(await screen.findByTestId("confirm-it-is-you")).toBeTruthy();
  expect(await screen.findByTestId("confirm-passkey")).toBeTruthy();
  await fireEvent.press(screen.getByTestId("confirm-passkey"));
}

describe("changing how you sign in", () => {
  it("changes the password after a passkey, sending the change again by itself", async () => {
    await show(await loggedInWithCode(), ChangePassword);
    await fireEvent.changeText(await screen.findByTestId("password-current"), DEMO_PASSWORD);
    await fireEvent.changeText(screen.getByTestId("password-new"), "lantern meadow quartz");
    await fireEvent.changeText(screen.getByTestId("password-again"), "lantern meadow quartz");
    await fireEvent.press(screen.getByTestId("password-change"));
    await confirmWithTheApp();
    expect(await screen.findByText("Password changed")).toBeTruthy();
  });

  it("says when the current password is wrong", async () => {
    const client = await loggedInWithCode();
    await client.confirmWithPasskey();
    await show(client, ChangePassword);
    await fireEvent.changeText(await screen.findByTestId("password-current"), "not my password");
    await fireEvent.changeText(screen.getByTestId("password-new"), "lantern meadow quartz");
    await fireEvent.changeText(screen.getByTestId("password-again"), "lantern meadow quartz");
    await fireEvent.press(screen.getByTestId("password-change"));
    expect(await screen.findByText(/do not match/)).toBeTruthy();
  });

  it("moves to a new number with the code texted to it", async () => {
    await show(await loggedInWithCode(), ChangePhone);
    await fireEvent.changeText(await screen.findByTestId("phone-new"), "701555013");
    await fireEvent.press(screen.getByTestId("phone-send"));
    await confirmWithTheApp();
    expect(await screen.findByText(/We texted a 6 digit code to/)).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("code-input"), DEMO_CODE);
    await fireEvent.press(screen.getByTestId("phone-confirm"));
    expect(await screen.findByText("Number changed")).toBeTruthy();
  });

  it("sets a new email address and says a link is on its way", async () => {
    const client = await loggedInWithCode();
    await client.confirmWithAuthenticator(DEMO_CODE);
    await show(client, ChangeEmail);
    await fireEvent.changeText(await screen.findByTestId("email-input"), "nakato@example.com");
    await fireEvent.press(screen.getByTestId("email-change"));
    expect(await screen.findByText("Check your inbox")).toBeTruthy();
    expect(screen.getByText(/We emailed a link to nakato@example.com/)).toBeTruthy();
  });
});
