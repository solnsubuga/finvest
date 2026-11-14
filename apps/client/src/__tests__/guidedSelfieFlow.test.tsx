import { act, fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { HOLD_MS, type FaceReading } from "@/identity/guidance";
import { IdentityClient, IdentityError } from "@/identity/types";
import { faceCamera } from "@/testing/fakeGuideCamera";
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
      // The guided selfie, as the API has with Regula.
      const client = actual.createStubIdentityClient({
        ...options,
        delayMs: 0,
        liveness: false,
        guided: true,
        now: () => Date.parse("2026-11-13T09:00:00Z"),
      });
      made.push(client);
      return client;
    },
  };
});

const mockTakePhoto = jest.fn(async () => ({ uri: "file:///photo.jpg", name: "photo.jpg", mimeType: "image/jpeg", size: 2048 }));
jest.mock("@/identity/pickPhoto", () => ({
  takePhoto: () => mockTakePhoto(),
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

beforeEach(() => {
  made.length = 0;
});

afterEach(() => jest.restoreAllMocks());

function face(overrides: Partial<FaceReading> = {}): FaceReading {
  return { faces: 1, width: 0.45, offsetX: 0, offsetY: 0, yaw: 0, pitch: 0, leftEyeOpen: 0.9, rightEyeOpen: 0.9, ...overrides };
}

/** The camera sees this, then still sees it once the hold before a picture has passed. */
async function hold(reading: FaceReading) {
  await act(async () => faceCamera.see(reading));
  // The router's tests run on fake timers, which also move Date.now.
  await act(async () => {
    jest.advanceTimersByTime(HOLD_MS + 50);
  });
  await act(async () => faceCamera.see(reading));
}

async function top(testId: string) {
  const all = await screen.findAllByTestId(testId);
  return all[all.length - 1]!;
}

async function upToTheSelfie(documentNumber = "CF94012345") {
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
  expect(await screen.findByText("5 of 6")).toBeTruthy();
}

beforeAll(warmRouter, WARM_ROUTER_MS);

describe("the guided selfie", () => {
  it("guides the selfie and four head turns, takes each when right, then hands the check in", async () => {
    await upToTheSelfie();
    await screen.findByTestId("fake-guide-camera");

    // The selfie: guided until the face is close and straight, then taken by itself.
    await act(async () => faceCamera.see(face({ width: 0.15 })));
    expect((await top("selfie-prompt")).props.children).toBe("Move a little closer");
    await act(async () => faceCamera.see(face({ yaw: 20 })));
    expect((await top("selfie-prompt")).props.children).toBe("Look straight at the camera");
    await hold(face());

    expect(await screen.findByText("Now four quick head turns")).toBeTruthy();
    expect(screen.getByText("6 of 6")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("poses-start"));

    expect(await screen.findByText("Turn your head to your left")).toBeTruthy();
    await act(async () => faceCamera.see(face({ yaw: 8 })));
    expect((await top("pose-left-prompt")).props.children).toBe("Keep turning to your left");
    await hold(face({ yaw: 30 }));

    // The phone measured the left turn as positive, so a positive right turn is the same way.
    expect(await screen.findByText("Turn your head to your right")).toBeTruthy();
    await hold(face({ yaw: 30 }));
    expect((await top("pose-right-prompt")).props.children).toBe("Other way: turn to your right");
    await hold(face({ yaw: -30 }));

    // The demo refuses the first look up, as the API would a frame not tilted far enough.
    expect(await screen.findByText("Look up a little")).toBeTruthy();
    await hold(face({ pitch: 20 }));
    expect(await screen.findByText("Tilt your chin further up.")).toBeTruthy();
    await hold(face({ pitch: 24 }));

    expect(await screen.findByText("Look down a little")).toBeTruthy();
    await hold(face({ pitch: -20 }));

    expect(await screen.findByText("Thanks, Peter. We are checking now.")).toBeTruthy();
  });

  it("sends the same picture again when it could not be checked", async () => {
    await upToTheSelfie();
    await screen.findByTestId("fake-guide-camera");
    const client = made[made.length - 1]!;
    jest.spyOn(client, "sendPhoto").mockRejectedValueOnce(new IdentityError("unavailable"));
    await hold(face());
    expect(await screen.findByText(/We could not check that picture just now/)).toBeTruthy();
    await fireEvent.press(await top("selfie-retry"));
    expect(await screen.findByText("Now four quick head turns")).toBeTruthy();
  });

  it("offers a button when faces cannot be found, and the camera app when there is no camera", async () => {
    await upToTheSelfie();
    await screen.findByTestId("fake-guide-camera");
    await act(async () => faceCamera.fail("no_detector"));
    await fireEvent.press(await top("selfie-take"));
    expect(await screen.findByText("Now four quick head turns")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("poses-start"));
    await screen.findByText("Turn your head to your left");
    await act(async () => faceCamera.fail("no_permission"));
    expect(await screen.findByText(/Finvest cannot use your camera/)).toBeTruthy();
    await fireEvent.press(await top("pose-left-camera-app"));
    expect(mockTakePhoto).toHaveBeenCalled();
    expect(await screen.findByText("Turn your head to your right")).toBeTruthy();
  });

  it("takes the head turns again with a new selfie staff asked for, before it can be sent", async () => {
    // The demo's staff ask for a new selfie when the number ends in 1.
    await upToTheSelfie("CF94012341");
    await screen.findByTestId("fake-guide-camera");
    await hold(face());
    await fireEvent.press(await screen.findByTestId("poses-start"));
    for (const reading of [face({ yaw: 30 }), face({ yaw: -30 }), face({ pitch: 20 }), face({ pitch: 22 }), face({ pitch: -20 })]) {
      await hold(reading);
    }
    await fireEvent.press(await screen.findByTestId("id-check-home"));
    await fireEvent.press(await screen.findByText("We need a new photo"));

    expect(await screen.findByTestId("retake-send")).toBeDisabled();
    expect(screen.getByTestId("retake-poses")).toBeDisabled();
    await fireEvent.press(screen.getByTestId("retake-selfie"));
    await screen.findByTestId("fake-guide-camera");
    await hold(face());

    // Straight on to the turns, which the new selfie needs.
    await fireEvent.press(await screen.findByTestId("poses-start"));
    for (const reading of [face({ yaw: 30 }), face({ yaw: -30 }), face({ pitch: 20 }), face({ pitch: -20 })]) {
      await hold(reading);
    }
    await fireEvent.press(await top("retake-send"));
    expect(await screen.findByText("Thanks, Peter. We are checking now.")).toBeTruthy();
  });
});
