import { fireEvent, screen, within } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CODE, DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { intentFor, parseIntent, storedIntent } from "@/organisations/intentTypes";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

const mockStore: { saved: string | null } = { saved: null };
// The approved demo account has applications of its own; `fresh` gives it none.
const mockOrganisations = { fresh: false };

jest.mock("@/organisations/stubOrganisationsClient", () => {
  const actual = jest.requireActual("@/organisations/stubOrganisationsClient");
  return {
    ...actual,
    createStubOrganisationsClient: (account: string | null) =>
      actual.createStubOrganisationsClient(mockOrganisations.fresh ? null : account),
  };
});

jest.mock("@/organisations/intent", () => {
  const { parseIntent: parse, storedIntent: stored } = jest.requireActual("@/organisations/intentTypes");
  return {
    readIntent: async () => parse(mockStore.saved),
    writeIntent: async (phone: string) => {
      mockStore.saved = await stored(phone);
    },
    clearIntent: async () => {
      mockStore.saved = null;
    },
  };
});

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  digestStringAsync: async (_algorithm: string, text: string) =>
    jest.requireActual("node:crypto").createHash("sha256").update(text).digest("hex"),
}));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function signUp(phone: string, { forOrganisation }: { forOrganisation: boolean }) {
  await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Your money, working every day.");
  await fireEvent.press(screen.getByText("Create an account"));
  if (forOrganisation) {
    await fireEvent.press(await screen.findByTestId("signup-organisation"));
    expect(await screen.findByText("It starts with your own account")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("organisation-signup-start"));
    expect(await screen.findByText("Step 1 of 3, your own account")).toBeTruthy();
    // Already on the company path, so the link to it is gone.
    expect(screen.queryByTestId("signup-organisation")).toBeNull();
  }
  await fireEvent.changeText(await screen.findByTestId("phone-input"), phone);
  await fireEvent.changeText(screen.getByTestId("email-input"), "amara@example.com");
  await fireEvent.press(screen.getByTestId("terms-checkbox"));
  await fireEvent.press(screen.getByTestId("signup-submit"));
  await fireEvent.changeText(await screen.findByTestId("code-input"), DEMO_CODE);
  await fireEvent.press(screen.getByTestId("code-submit"));
  await fireEvent.changeText(await screen.findByTestId("password-input"), "green matooke sunday");
  await fireEvent.changeText(screen.getByTestId("password-confirm-input"), "green matooke sunday");
  await fireEvent.press(screen.getByTestId("password-submit"));
  await fireEvent.press(await screen.findByTestId("email-step-later"));
  await screen.findByTestId("home");
}

beforeAll(warmRouter, WARM_ROUTER_MS);
beforeEach(() => {
  mockStore.saved = null;
  mockOrganisations.fresh = false;
});

async function logIn() {
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
}

describe("opening a company or group account from sign up", () => {
  it("says the application waits for the ID check while it is not approved", async () => {
    await signUp("772 900 411", { forOrganisation: true });
    const list = await screen.findByTestId("home-set-up");
    expect(within(list).getByTestId("set-up-organisation")).toHaveTextContent(/Once your ID check is approved/);
    await fireEvent.press(within(list).getByTestId("set-up-organisation"));
    expect(await screen.findByText(/Finish your own ID check first/)).toBeTruthy();
    expect(screen.getByTestId("organisation-kind-continue")).toBeDisabled();
  });

  it("leads an approved client from Home to the application", async () => {
    mockOrganisations.fresh = true;
    mockStore.saved = await storedIntent("+256772123456");
    await renderRouter("./src/app", { initialUrl: "/login" });
    await logIn();
    const step = await screen.findByTestId("set-up-organisation");
    expect(step).toHaveTextContent(/Its registration papers and who signs for it/);
    await fireEvent.press(step);
    expect(await screen.findByText("What kind of account?")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("organisation-kind-company"));
    expect(screen.getByTestId("organisation-kind-continue")).toBeEnabled();
  });

  it("stops asking once the client has an application of their own", async () => {
    mockStore.saved = await storedIntent("+256772123456");
    await renderRouter("./src/app", { initialUrl: "/login" });
    await logIn();
    await screen.findByTestId("home-set-up");
    expect(screen.queryByTestId("set-up-organisation")).toBeNull();
    expect(mockStore.saved).toBeNull();
  });

  it("takes someone who already has an account to the application after logging in", async () => {
    await renderRouter("./src/app", { initialUrl: "/signup/organisation" });
    await fireEvent.press(await screen.findByText("I already have a Finvest account"));
    await logIn();
    expect(await screen.findByText("What kind of account?")).toBeTruthy();
  });

  it("asks nothing about companies after an ordinary sign up", async () => {
    mockStore.saved = await storedIntent("+256772900413");
    await signUp("772 900 413", { forOrganisation: false });
    await screen.findByTestId("home-set-up");
    expect(screen.queryByTestId("set-up-organisation")).toBeNull();
    expect(mockStore.saved).toBeNull();
  });
});

describe("the kept choice", () => {
  it("belongs only to the number that made it, and never holds the number", async () => {
    const raw = await storedIntent("+256772900413");
    expect(raw).not.toContain("772900413");
    const kept = parseIntent(raw);
    expect(await intentFor(kept, "+256772900413")).toBe(true);
    expect(await intentFor(kept, "+256772123456")).toBe(false);
    expect(await intentFor(kept, null)).toBe(false);
    expect(parseIntent("not json")).toBeNull();
    // An entry from before digests, holding the number itself, counts as none.
    expect(parseIntent(JSON.stringify({ phone: "+256772900413" }))).toBeNull();
  });
});
