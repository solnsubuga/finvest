import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

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
      // The camera selfie. The live check is covered in liveSelfieFlow.test.tsx.
      actual.createStubIdentityClient({
        ...options,
        delayMs: 0,
        liveness: false,
        now: () => Date.parse("2026-11-13T09:00:00Z"),
      }),
  };
});

// The camera and the photo library, with a photo of the right kind.
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

/** Signs in an account with no ID check yet, or the demo account, which is approved. */
async function signIn(phone: "701000200" | "772123456") {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), phone);
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  if (phone === "772123456") {
    await fireEvent.press(await screen.findByTestId("use-passkey"));
  } else {
    await fireEvent.press(await screen.findByTestId("use-authenticator"));
    await fireEvent.changeText(await screen.findByTestId("code-input"), "123456");
    await fireEvent.press(screen.getByTestId("code-submit"));
  }
}

/** The screen on top. The steps before it stay mounted underneath in the stack. */
async function top(testId: string) {
  const all = await screen.findAllByTestId(testId);
  return all[all.length - 1]!;
}

async function photograph() {
  await fireEvent.press(await top("photo-take"));
  await fireEvent.press(await top("photo-use"));
}

async function handIn(documentNumber: string) {
  await fireEvent.press(await screen.findByText("Confirm who you are"));
  expect(await screen.findByText("Which ID will you use?")).toBeTruthy();
  await fireEvent.press(await screen.findByTestId("document-continue"));

  expect(await screen.findByText("Front of your National ID")).toBeTruthy();
  expect(await screen.findByText("2 of 5")).toBeTruthy();
  await photograph();
  expect(await screen.findByText("Back of your National ID")).toBeTruthy();
  await photograph();

  await fireEvent.changeText(await screen.findByTestId("details-surname"), "OKELLO");
  expect(screen.getByText("4 of 5")).toBeTruthy();
  await fireEvent.changeText(screen.getByTestId("details-given-names"), "Peter");
  await fireEvent.changeText(screen.getByTestId("details-number"), documentNumber);
  await fireEvent.changeText(screen.getByTestId("details-born"), "14/11/2008");
  await fireEvent.changeText(screen.getByTestId("details-expires"), "18/09/2030");
  await fireEvent.press(screen.getByTestId("details-save"));
  // A day short of 18: refused before anything is sent.
  expect(await screen.findByText("You must be 18 or older to open a Finvest account.")).toBeTruthy();
  await fireEvent.changeText(screen.getByTestId("details-born"), "01/02/1990");
  await fireEvent.press(screen.getByTestId("details-save"));

  expect(await screen.findByText("5 of 5")).toBeTruthy();
  await photograph();
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("the identity check", () => {
  it("hands in an ID, details and a selfie, then shows the check under way", async () => {
    await signIn("701000200");
    await handIn("CF94012345");
    expect(await screen.findByText("Thanks, Peter. We are checking now.")).toBeTruthy();

    // Home says so too, and opens the same status.
    await fireEvent.press(screen.getByTestId("id-check-home"));
    expect(await screen.findByText("We are checking your ID")).toBeTruthy();
  });

  it("takes only the photo staff asked for again, then sends it", async () => {
    await signIn("701000200");
    // The demo's staff ask for a new selfie when the number ends in 1.
    await handIn("CF94012341");
    await fireEvent.press(await screen.findByTestId("id-check-home"));

    await fireEvent.press(await screen.findByText("We need a new photo"));
    expect(await screen.findByTestId("retake-message")).toBeTruthy();
    expect(screen.getByTestId("retake-send")).toBeDisabled();

    await fireEvent.press(screen.getByTestId("retake-selfie"));
    expect(await screen.findByText(/Your selfie is too dark/)).toBeTruthy();
    await photograph();

    await fireEvent.press(await screen.findByTestId("retake-send"));
    expect(await screen.findByText("Thanks, Peter. We are checking now.")).toBeTruthy();
  });

  it("shows an approved client their account number from Profile", async () => {
    await signIn("772123456");
    await screen.findByText("Welcome, Amara");
    // Approved: Home no longer asks for the check.
    expect(screen.queryByTestId("home-id-check")).toBeNull();
    await fireEvent.press(screen.getByLabelText("Profile"));
    await fireEvent.press(await screen.findByTestId("profile-id-check"));
    expect(await screen.findByText("You are verified, Amara")).toBeTruthy();
    expect(screen.getByTestId("account-number")).toHaveTextContent("1001 004 827");
  });
});
