import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CLAIM } from "@/claim/demo";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

/** The address the app was opened with, as the link in our SMS or email would open it. */
const mockLink: { url: string | null } = { url: null };

jest.mock("@/auth/emailLink", () => {
  const actual = jest.requireActual("@/auth/emailLink");
  return { ...actual, useLinkToken: () => actual.tokenFromLink(mockLink.url) };
});

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/claim/stubClaimClient", () => {
  const actual = jest.requireActual("@/claim/stubClaimClient");
  return { ...actual, createStubClaimClient: (options: object) => actual.createStubClaimClient({ ...options, delayMs: 0 }) };
});

jest.mock("@/identity/stubIdentityClient", () => {
  const actual = jest.requireActual("@/identity/stubIdentityClient");
  return {
    ...actual,
    createStubIdentityClient: (options: object) =>
      actual.createStubIdentityClient({ ...options, delayMs: 0, liveness: false, now: () => Date.parse("2026-11-13T09:00:00Z") }),
  };
});

jest.mock("@/identity/pickPhoto", () => {
  const photo = { uri: "file:///photo.jpg", name: "photo.jpg", mimeType: "image/jpeg", size: 2048 };
  return { takePhoto: jest.fn(async () => photo), choosePhoto: jest.fn(async () => photo) };
});

jest.mock("@/identity/photo", () => ({
  ...jest.requireActual("@/identity/photo"),
  kampalaToday: () => "2026-11-13",
}));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  mockLink.url = null;
});

/** The screen on top. The steps before it stay mounted underneath in the stack. */
async function top(testId: string) {
  const all = await screen.findAllByTestId(testId);
  return all[all.length - 1]!;
}

async function claim(token: string) {
  mockLink.url = `finvest://claim#${token}`;
  await renderRouter("./src/app", { initialUrl: "/claim" });
  expect(await screen.findByText("Your Finvest investments are now in the app")).toBeTruthy();
  await fireEvent.press(screen.getByTestId("claim-start"));
  expect(await screen.findByText("Enter the code and choose a password")).toBeTruthy();
  const open = screen.getByTestId("claim-complete");
  await fireEvent.changeText(screen.getByTestId("code-input"), "123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), "a long new password");
  // Not until both boxes match.
  expect(open.props.accessibilityState?.disabled).toBe(true);
  await fireEvent.changeText(screen.getByTestId("password-confirm-input"), "a long new password");
  await fireEvent.press(open);
}

async function photograph() {
  await fireEvent.press(await top("photo-take"));
  await fireEvent.press(await top("photo-use"));
}

/** Home, then the move again, as a client coming back later would. Each reads the claim. */
async function comeBack() {
  await goHome();
  await fireEvent.press(await screen.findByTestId("home-claim"));
}

async function goHome() {
  await act(async () => {
    router.navigate("/");
  });
}

/** The ID check from the move: the document, both photos, the details and the selfie. */
async function idCheck() {
  await fireEvent.press(await top("claim-id-check"));
  expect(await screen.findByText("Which ID will you use?")).toBeTruthy();
  await fireEvent.press(await screen.findByTestId("document-continue"));
  await photograph();
  await photograph();
  await fireEvent.changeText(await screen.findByTestId("details-surname"), "SSEMWANGA");
  await fireEvent.changeText(screen.getByTestId("details-given-names"), "Peter");
  await fireEvent.changeText(screen.getByTestId("details-number"), "CF94012345");
  await fireEvent.changeText(screen.getByTestId("details-born"), "01/02/1990");
  await fireEvent.changeText(screen.getByTestId("details-expires"), "18/09/2030");
  await fireEvent.press(screen.getByTestId("details-save"));
  // The selfie is the last step, and it hands the check in with the claim.
  await photograph();
  expect(await screen.findByText("Thanks, Peter. We are checking now.")).toBeTruthy();
  await fireEvent.press(screen.getByTestId("id-check-home"));
  expect(await screen.findByText("We are checking it is you")).toBeTruthy();
}

describe("claiming an account moved from paper records", () => {
  it("opens the account, takes the full ID check and confirms the investments", async () => {
    await claim(DEMO_CLAIM.token);
    // Opening the account leads straight to the move.
    expect(await screen.findByText(/we check your ID, as we do for everyone who joins/)).toBeTruthy();
    await idCheck();

    // The demo's stand-in for staff approves it: Home leads on to the investments.
    await comeBack();
    await goHome();
    expect(await screen.findByText("Check your investments")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("home-claim"));
    await fireEvent.press(await top("claim-confirm"));
    expect(await screen.findByText("Your account is ready")).toBeTruthy();
    await fireEvent.press(await top("claim-home"));
    expect(screen.queryByTestId("home-claim")).toBeNull();
  });

  it("takes a description of what is wrong instead of a confirmation", async () => {
    await claim(DEMO_CLAIM.token);
    await idCheck();
    await comeBack();
    await comeBack();

    await fireEvent.press(await top("claim-problem"));
    expect(await top("claim-problem-send")).toBeDisabled();
    await fireEvent.changeText(await top("claim-problem-input"), "   ");
    expect(await top("claim-problem-send")).toBeDisabled();
    await fireEvent.changeText(await top("claim-problem-input"), "My second bill is missing");
    await fireEvent.press(await top("claim-problem-send"));
    expect(await screen.findByText("Thank you for telling us")).toBeTruthy();
  });

  it("refuses a link that no longer works, and says what to do", async () => {
    mockLink.url = "finvest://claim#an-old-link-that-was-replaced";
    await renderRouter("./src/app", { initialUrl: "/claim" });
    await fireEvent.press(await screen.findByTestId("claim-start"));
    expect(await screen.findByText(/This link no longer works/)).toBeTruthy();
  });

  it("asks for the link when the page is opened without one", async () => {
    await renderRouter("./src/app", { initialUrl: "/claim" });
    expect(await screen.findByText("Open the link from our message")).toBeTruthy();
  });
});
