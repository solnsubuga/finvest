import { fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** Whether the client's ID has been sent, so the API names them. */
const mockId = { sent: false, reads: 0 };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  const unnamed = (value: unknown) =>
    value && typeof value === "object" && "displayName" in value && !mockId.sent
      ? { ...value, displayName: null, firstName: null }
      : value;
  return {
    ...actual,
    createStubAuthClient: () => {
      const client = actual.createStubAuthClient({ delayMs: 0 });
      const wrapped: Record<string, unknown> = {};
      for (const [key, method] of Object.entries(client)) {
        wrapped[key] =
          typeof method === "function"
            ? async (...args: unknown[]) => {
                if (key === "restoreSession") mockId.reads += 1;
                return unnamed(await (method as (...a: unknown[]) => Promise<unknown>)(...args));
              }
            : method;
      }
      return wrapped;
    },
  };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("the greeting", () => {
  it("names the client once their ID is sent, without logging in again", async () => {
    await renderRouter("./src/app", { initialUrl: "/" });
    await fireEvent.press(await screen.findByText("Log in"));
    await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
    await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
    await fireEvent.press(screen.getByTestId("login-submit"));
    await fireEvent.press(await screen.findByTestId("use-passkey"));
    await screen.findByTestId("home");
    expect(screen.queryByText("Amara Nakato")).toBeNull();

    // The ID is sent elsewhere in the app. Coming back to Home reads the session again, as
    // Home does for a confirmed email, and the API names her from the ID she sent.
    mockId.sent = true;
    const before = mockId.reads;
    router.push("/insights");
    await screen.findByText("Coming up");
    router.back();
    expect(await screen.findByText("Amara Nakato")).toBeTruthy();
    expect(mockId.reads).toBeGreaterThan(before);
  }, 30_000);
});
