import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { LIVENESS_POLL_MS } from "@/identity/LiveSelfie";
import { IdentityClient, IdentityError } from "@/identity/types";
import { regulaSession } from "@/testing/fakeRegula";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

jest.mock("@/claim/stubClaimClient", () => {
  const actual = jest.requireActual("@/claim/stubClaimClient");
  return { ...actual, createStubClaimClient: (options: object) => actual.createStubClaimClient({ ...options, delayMs: 0 }) };
});

/** The demo identity clients the app made, so a test can make one of their sends fail. */
const made: IdentityClient[] = [];

jest.mock("@/identity/stubIdentityClient", () => {
  const actual = jest.requireActual("@/identity/stubIdentityClient");
  return {
    ...actual,
    createStubIdentityClient: (options: object) => {
      // As the API has with Regula: Regula's liveness in the app, then the guided head turns.
      const client = actual.createStubIdentityClient({
        ...options,
        delayMs: 0,
        liveness: true,
        guided: true,
        regula: true,
        now: () => Date.parse("2026-11-13T09:00:00Z"),
      });
      made.push(client);
      return client;
    },
  };
});

jest.mock("@/identity/pickPhoto", () => ({
  takePhoto: async () => ({ uri: "file:///photo.jpg", name: "photo.jpg", mimeType: "image/jpeg", size: 2048 }),
  choosePhoto: async () => ({ uri: "file:///photo.jpg", name: "photo.jpg", mimeType: "image/jpeg", size: 2048 }),
}));

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

const TRANSACTION = "0f6c2b1e-5a4d-4c3b-9a2f-1e0d9c8b7a65";

beforeEach(() => {
  made.length = 0;
});

afterEach(() => jest.restoreAllMocks());

async function top(testId: string) {
  const all = await screen.findAllByTestId(testId);
  return all[all.length - 1]!;
}

/** Lets the screen read the live check again, as it does every few seconds while one waits. */
async function nextRead() {
  await act(async () => {
    jest.advanceTimersByTime(LIVENESS_POLL_MS + 10);
  });
}

/** Signs in a client with no check and fills in every step before the selfie. */
async function upToTheSelfie(documentNumber = "CF94012345", live = true) {
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
  for (const side of ["Front", "Back"]) {
    expect(await screen.findByText(`${side} of your National ID`)).toBeTruthy();
    await fireEvent.press(await top("photo-take"));
    await fireEvent.press(await top("photo-use"));
  }
  await fireEvent.changeText(await screen.findByTestId("details-surname"), "OKELLO");
  await fireEvent.changeText(screen.getByTestId("details-given-names"), "Peter");
  await fireEvent.changeText(screen.getByTestId("details-number"), documentNumber);
  await fireEvent.changeText(screen.getByTestId("details-born"), "01/02/1990");
  await fireEvent.changeText(screen.getByTestId("details-expires"), "18/09/2030");
  await fireEvent.press(screen.getByTestId("details-save"));
  if (live) expect(await screen.findByText("Take a live selfie")).toBeTruthy();
}

/** Starts a live check and waits until Regula's session is open for it. */
async function startSession() {
  await fireEvent.press(await top("live-start"));
  for (let read = 0; read < 4 && !regulaSession.open; read++) await nextRead();
  expect(regulaSession.open).toBe(true);
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("Regula's liveness in the app", () => {
  it("runs Regula with the attempt's tag, reports the transaction, then goes on to the head turns", async () => {
    await upToTheSelfie();
    expect(screen.getByText(/A short check by Regula/)).toBeTruthy();
    await startSession();
    expect(regulaSession.tag).toMatch(/^demo-liveness-\d+$/);
    expect(regulaSession.serviceUrl).toBe("https://face.regula.test");

    const client = made[made.length - 1]!;
    const report = jest.spyOn(client, "reportLivenessTransaction");
    await act(async () => regulaSession.end({ kind: "done", transactionId: TRANSACTION }));
    expect(report).toHaveBeenCalledWith(expect.stringMatching(/^demo-liveness-/), TRANSACTION);
    expect(await screen.findByText("Checking your live selfie")).toBeTruthy();

    for (let read = 0; read < 4 && !screen.queryByText("Live selfie done"); read++) await nextRead();
    expect(await screen.findByText("Live selfie done")).toBeTruthy();
    expect(screen.getByText(/Next come four quick head turns/)).toBeTruthy();
    await fireEvent.press(await top("live-continue"));
    expect(await screen.findByText("Now four quick head turns")).toBeTruthy();
    expect(screen.getByText("6 of 6")).toBeTruthy();
  });

  it("does not open a closed session again by itself, and opens it when asked", async () => {
    await upToTheSelfie();
    await startSession();
    await act(async () => regulaSession.end({ kind: "cancelled" }));
    expect(await screen.findByText("Your live selfie did not finish")).toBeTruthy();
    await nextRead();
    expect(regulaSession.open).toBe(false);

    await fireEvent.press(await top("live-open"));
    expect(regulaSession.open).toBe(true);
  });

  it("starts a new attempt for one that was already open when the screen opened", async () => {
    await upToTheSelfie();
    await startSession();
    const client = made[made.length - 1]!;
    const first = (await client.getLiveness()).latest!.id;
    // The client leaves mid-session and comes back, as after a reload. The attempt is still open,
    // but its session may already have run, and an attempt takes one session.
    await act(async () => router.replace("/id-check"));
    await act(async () => router.push("/id-check/photo/selfie"));
    expect(await screen.findByText("Your live selfie did not finish")).toBeTruthy();
    expect(regulaSession.open).toBe(false);

    await fireEvent.press(await top("live-open"));
    for (let read = 0; read < 4 && !regulaSession.open; read++) await nextRead();
    expect(regulaSession.open).toBe(true);
    expect((await client.getLiveness()).latest!.id).not.toBe(first);
  });

  it("runs an attempt that was still starting when the screen opened", async () => {
    await upToTheSelfie();
    const client = made[made.length - 1]!;
    const started = await client.startLiveness();
    // The client comes back while the attempt is still being set up: it never had a session.
    const real = client.getLiveness.bind(client);
    jest.spyOn(client, "getLiveness").mockImplementationOnce(async () => {
      const state = await real();
      return { ...state, latest: { ...state.latest!, status: "starting", url: null } };
    });
    await act(async () => router.replace("/id-check"));
    await act(async () => router.push("/id-check/photo/selfie"));
    for (let read = 0; read < 4 && !regulaSession.open; read++) await nextRead();
    expect(regulaSession.open).toBe(true);
    expect(regulaSession.tag).toBe(started.id);
  });

  it("says why a session could not run", async () => {
    await upToTheSelfie();
    await startSession();
    await act(async () => regulaSession.end({ kind: "trouble", reason: "no_permission" }));
    expect(await screen.findByText(/Finvest needs your camera for the live selfie/)).toBeTruthy();
  });

  it("keeps a transaction that could not be sent, and sends it again", async () => {
    await upToTheSelfie();
    await startSession();
    const client = made[made.length - 1]!;
    jest.spyOn(client, "reportLivenessTransaction").mockRejectedValueOnce(new IdentityError("offline"));
    await act(async () => regulaSession.end({ kind: "done", transactionId: TRANSACTION }));
    expect(await screen.findByText("We could not send your live selfie")).toBeTruthy();

    await fireEvent.press(await top("live-resend"));
    expect(client.reportLivenessTransaction).toHaveBeenLastCalledWith(expect.any(String), TRANSACTION);
    expect(await screen.findByText("Checking your live selfie")).toBeTruthy();
  });

  it("offers another try when Regula did not see a live person", async () => {
    // A number ending in 4 is not a live person in the demo.
    await upToTheSelfie("CF94012344");
    await startSession();
    await act(async () => regulaSession.end({ kind: "done", transactionId: TRANSACTION }));
    for (let read = 0; read < 4 && !screen.queryByText("We could not finish the live check"); read++) await nextRead();
    expect(await screen.findByText("We could not finish the live check")).toBeTruthy();
    expect(screen.getByText("9 tries left.")).toBeTruthy();
  });

  it("takes the guided selfie with the camera instead when the client asks", async () => {
    await upToTheSelfie();
    await fireEvent.press(await top("live-camera"));
    expect(await screen.findByText("Take a selfie")).toBeTruthy();
    expect(await screen.findByTestId("fake-guide-camera")).toBeTruthy();
  });

  it("takes the guided selfie with the camera when the build does not know Regula's service", async () => {
    const url = process.env.EXPO_PUBLIC_REGULA_FACE_URL;
    delete process.env.EXPO_PUBLIC_REGULA_FACE_URL;
    try {
      await upToTheSelfie("CF94012345", false);
      expect(await screen.findByTestId("fake-guide-camera")).toBeTruthy();
      expect(screen.queryByText("Take a live selfie")).toBeNull();
    } finally {
      process.env.EXPO_PUBLIC_REGULA_FACE_URL = url;
    }
  });
});
