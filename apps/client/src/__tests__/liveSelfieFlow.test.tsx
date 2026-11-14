import { fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";
import { Linking } from "react-native";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { DEMO_LIVENESS_PAGE } from "@/identity/stubIdentityClient";
import { IdentityClient } from "@/identity/types";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

/** The demo identity clients the app made, so a test can make one of their reads fail. */
const made: IdentityClient[] = [];

jest.mock("@/claim/stubClaimClient", () => {
  const actual = jest.requireActual("@/claim/stubClaimClient");
  return { ...actual, createStubClaimClient: (options: object) => actual.createStubClaimClient({ ...options, delayMs: 0 }) };
});

jest.mock("@/identity/stubIdentityClient", () => {
  const actual = jest.requireActual("@/identity/stubIdentityClient");
  return {
    ...actual,
    createStubIdentityClient: (options: object) => {
      const client = actual.createStubIdentityClient({
        ...options,
        delayMs: 0,
        liveness: true,
        now: () => Date.parse("2026-11-13T09:00:00Z"),
      });
      made.push(client);
      return client;
    },
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

/** Taps "I have finished" until the demo's live check answers, as the worker would read it. */
async function waitForAnswer(text: string) {
  for (let read = 0; read < 5 && !screen.queryByText(text); read++) {
    await fireEvent.press(await top("live-finished"));
  }
  expect(await screen.findByText(text)).toBeTruthy();
}

async function top(testId: string) {
  const all = await screen.findAllByTestId(testId);
  return all[all.length - 1]!;
}

async function photograph() {
  await fireEvent.press(await top("photo-take"));
  await fireEvent.press(await top("photo-use"));
}

/** Signs in a client with no check and takes them to the selfie step. */
async function toSelfie(documentNumber: string) {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "701000200");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-authenticator"));
  await fireEvent.changeText(await screen.findByTestId("code-input"), "123456");
  await fireEvent.press(screen.getByTestId("code-submit"));

  await fireEvent.press(await screen.findByText("Confirm who you are"));
  await fireEvent.press(await screen.findByTestId("document-continue"));
  await photograph();
  await photograph();
  await fireEvent.changeText(await screen.findByTestId("details-surname"), "OKELLO");
  await fireEvent.changeText(screen.getByTestId("details-given-names"), "Peter");
  await fireEvent.changeText(screen.getByTestId("details-number"), documentNumber);
  await fireEvent.changeText(screen.getByTestId("details-born"), "01/02/1990");
  await fireEvent.changeText(screen.getByTestId("details-expires"), "18/09/2030");
  await fireEvent.press(screen.getByTestId("details-save"));
  expect(await screen.findByText("Take a live selfie")).toBeTruthy();
}

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  jest.spyOn(Linking, "openURL").mockResolvedValue(undefined);
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("the live selfie", () => {
  it("opens the partner's page, waits for a live person and hands the check in", async () => {
    await toSelfie("CF94012345");
    await fireEvent.press(await top("live-start"));
    expect(await screen.findByText("Your live check is ready")).toBeTruthy();

    await fireEvent.press(await top("live-open"));
    expect(Linking.openURL).toHaveBeenCalledWith(DEMO_LIVENESS_PAGE);
    expect(await screen.findByText("Finish the check, then come back")).toBeTruthy();
    expect(screen.getByTestId("live-waiting")).toBeTruthy();

    await waitForAnswer("Live selfie done");

    await fireEvent.press(await top("live-continue"));
    expect(await screen.findByText("Thanks, Peter. We are checking now.")).toBeTruthy();
  });

  it("keeps waiting for the result when one read fails", async () => {
    await toSelfie("CF94012345");
    await fireEvent.press(await top("live-start"));
    await fireEvent.press(await top("live-open"));
    expect(await screen.findByText("Finish the check, then come back")).toBeTruthy();

    // A weak connection drops the next read. The screen still moves on by itself after it.
    const client = made[made.length - 1]!;
    jest.spyOn(client, "getLiveness").mockRejectedValueOnce(new TypeError("Network request failed"));
    expect(await screen.findByText("Live selfie done", {}, { timeout: 20_000 })).toBeTruthy();
    expect(client.getLiveness).toHaveBeenCalledTimes(4);
  }, 30_000);

  it("offers another try or a camera photo when no live person was seen", async () => {
    // The demo's live check does not see a live person when the number ends in 4.
    await toSelfie("CF94012344");
    await fireEvent.press(await top("live-start"));
    await fireEvent.press(await top("live-open"));
    await waitForAnswer("We could not finish the live check");
    expect(screen.getByText("9 tries left.")).toBeTruthy();
    expect(screen.getByText("Try again")).toBeTruthy();

    await fireEvent.press(await top("live-camera"));
    expect(await top("photo-take")).toBeTruthy();
    await photograph();
    expect(await screen.findByText("Thanks, Peter. We are checking now.")).toBeTruthy();
  });
});
