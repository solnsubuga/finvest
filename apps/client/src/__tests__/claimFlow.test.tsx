import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_CLAIM_FULL_CHECK, DEMO_CLAIM_NOT_MATCHED, DEMO_CLAIM_SELFIE } from "@/claim/demo";
import { DEMO_CLAIM_RETAKE_MESSAGE } from "@/claim/stubClaimClient";
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
  expect(await screen.findByText("Your account has moved to Finvest 2.0")).toBeTruthy();
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

describe("claiming an account moved from Finvest 1.0", () => {
  it("opens the account, matches a selfie, takes one photo again and confirms the investments", async () => {
    await claim(DEMO_CLAIM_SELFIE.token);
    expect((await screen.findAllByText("Confirm it is you")).length).toBeGreaterThan(0);

    await fireEvent.press(await screen.findByTestId("claim-photos-start"));
    expect(await screen.findByText("Front of your ID")).toBeTruthy();
    await photograph();
    expect((await screen.findAllByText("Take a selfie")).length).toBeGreaterThan(0);
    await photograph();
    await fireEvent.press(await top("claim-hand-in"));
    expect(await screen.findByText("We are checking it is you")).toBeTruthy();

    // The demo's stand-in for staff asks for the selfie again.
    await comeBack();
    expect(await screen.findByText("We need a new photo")).toBeTruthy();
    expect(screen.getByText(DEMO_CLAIM_RETAKE_MESSAGE)).toBeTruthy();
    expect(await top("claim-retake-send")).toBeDisabled();
    await fireEvent.press(await top("claim-photo-selfie"));
    await photograph();
    await fireEvent.press(await top("claim-retake-send"));
    expect(await screen.findByText("We are checking it is you")).toBeTruthy();

    // Matched: Home leads on to the investments, and confirming them is the end.
    await goHome();
    expect(await screen.findByText("Check your investments")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("home-claim"));
    await fireEvent.press(await top("claim-confirm"));
    expect(await screen.findByText("You have moved to Finvest 2.0")).toBeTruthy();
    await fireEvent.press(await top("claim-home"));
    expect(screen.queryByTestId("home-claim")).toBeNull();
  });

  it("takes a description of what is wrong instead of a confirmation", async () => {
    await claim(DEMO_CLAIM_SELFIE.token);
    await fireEvent.press(await screen.findByTestId("claim-photos-start"));
    await photograph();
    await photograph();
    await fireEvent.press(await top("claim-hand-in"));
    await comeBack();
    await fireEvent.press(await top("claim-photo-selfie"));
    await photograph();
    await fireEvent.press(await top("claim-retake-send"));
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

  it("hands the full ID check in with the claim, for a client Finvest 1.0 never checked", async () => {
    await claim(DEMO_CLAIM_FULL_CHECK.token);
    expect(await screen.findByText("Confirm who you are")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("claim-id-check"));
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

    // The move waits for the decision too.
    await fireEvent.press(screen.getByTestId("id-check-home"));
    expect(await screen.findByText("We are checking it is you")).toBeTruthy();
  });

  it("sends a client whose photos do not match to the full ID check", async () => {
    await claim(DEMO_CLAIM_NOT_MATCHED.token);
    await fireEvent.press(await screen.findByTestId("claim-photos-start"));
    await photograph();
    await photograph();
    await fireEvent.press(await top("claim-hand-in"));
    expect(await screen.findByText("We are checking it is you")).toBeTruthy();

    // The demo's stand-in for staff cannot match them: the move and Home lead to the ID check.
    await comeBack();
    expect((await screen.findAllByText("We need a full ID check")).length).toBeGreaterThan(0);
    await goHome();
    expect(await screen.findByText("We could not match your photos. An ID check finishes moving from Finvest 1.0.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("home-claim"));
    await fireEvent.press(await top("claim-id-check"));
    expect(await screen.findByText("Which ID will you use?")).toBeTruthy();
  });
});
