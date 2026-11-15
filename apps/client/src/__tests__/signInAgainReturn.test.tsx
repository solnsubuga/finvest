import { fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** How many more downloads the backend refuses as needing a fresh log in. */
const mockStale = { downloads: 0 };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/profile/stubProfileClient", () => {
  const actual = jest.requireActual("@/profile/stubProfileClient");
  return {
    ...actual,
    createStubProfileClient: (account: string | null) => {
      const client = actual.createStubProfileClient(account);
      return {
        ...client,
        downloadMyData: async () => {
          if (mockStale.downloads > 0) {
            mockStale.downloads -= 1;
            const { ProfileError } = jest.requireActual("@/profile/types");
            throw new ProfileError("sign_in_again");
          }
          return client.downloadMyData();
        },
      };
    },
  };
});

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function logIn() {
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("logging in again", () => {
  it("says why, and brings the client back to downloading their data", async () => {
    await renderRouter("./src/app", { initialUrl: "/" });
    await logIn();
    await screen.findByTestId("home");
    router.push("/account/language");
    mockStale.downloads = 1;
    await fireEvent.press(await screen.findByTestId("language-download"));

    await screen.findByText("Log in again first");
    expect(
      screen.getByText(
        "For your safety, log in again before you download a copy of your data. We will bring you straight back here.",
      ),
    ).toBeTruthy();
    await fireEvent.press(screen.getByTestId("sign-in-again"));
    await screen.findByText("Your money, working every day.");
    await logIn();

    // Back on Language and data, not Home.
    await screen.findByTestId("language-download");
    expect(screen).toHavePathname("/account/language");
  }, 30_000);
});
