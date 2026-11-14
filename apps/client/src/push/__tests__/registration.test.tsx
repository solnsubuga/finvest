import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";
import { AuthProvider } from "@/auth/AuthProvider";
import { createStubAuthClient } from "@/auth/stubAuthClient";

import { PushDevice, PushPermission } from "../device";
import { PushOnThisPhone } from "../PushOnThisPhone";
import { forgetPush, pushState, registeredPushToken, registerPush, setPushDevice } from "../registration";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

const TOKEN = "ExponentPushToken[abcdefghijklmnop]";

function fakePhone(start: PushPermission, answer: PushPermission = "granted", token: () => Promise<string> = async () => TOKEN) {
  const phone = { permission: start, asked: 0 };
  const device: PushDevice = {
    platform: "android",
    permission: async () => phone.permission,
    ask: async () => {
      phone.asked += 1;
      phone.permission = answer;
      return answer;
    },
    token,
  };
  setPushDevice(device);
  return phone;
}

function serve(status = 204) {
  const seen: { key: string; body: unknown }[] = [];
  setFetch(async (url, init) => {
    seen.push({
      key: `${init?.method} ${String(url).replace(API_URL, "")}`,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(status === 204 ? null : JSON.stringify({ request_id: "R".repeat(20), error: { code: "unavailable", message: "x", fields: [] } }), { status });
  });
  return seen;
}

afterEach(() => {
  setFetch(null);
  setPushDevice(null);
});

describe("registering this phone for push", () => {
  it("sends the token in the body after sign-in when push is already allowed, and never asks", async () => {
    const phone = fakePhone("granted");
    const seen = serve();
    await expect(registerPush({ ask: false })).resolves.toEqual({ status: "on" });
    expect(phone.asked).toBe(0);
    expect(seen).toEqual([{ key: "PUT /client/push-devices", body: { token: TOKEN, platform: "android" } }]);
    expect(registeredPushToken()).toBe(TOKEN);
  });

  it("does nothing after sign-in when the client has not been asked", async () => {
    const phone = fakePhone("undetermined");
    const seen = serve();
    await expect(registerPush({ ask: false })).resolves.toEqual({ status: "off" });
    expect(phone.asked).toBe(0);
    expect(seen).toEqual([]);
  });

  it("asks only when told to, and respects a no", async () => {
    const phone = fakePhone("undetermined", "denied");
    const seen = serve();
    await expect(registerPush({ ask: true })).resolves.toEqual({ status: "blocked" });
    expect(phone.asked).toBe(1);
    expect(seen).toEqual([]);
  });

  it("says unsupported on the web and in a build with no Expo project", async () => {
    setPushDevice({ platform: null, permission: async () => "unsupported", ask: async () => "unsupported", token: async () => TOKEN });
    await expect(registerPush({ ask: true })).resolves.toEqual({ status: "unsupported" });
    fakePhone("granted", "granted", async () => {
      throw new Error("no_expo_project");
    });
    await expect(registerPush({ ask: true })).resolves.toEqual({ status: "unsupported" });
  });

  it("says failed, and keeps no token, when the API cannot take it", async () => {
    fakePhone("granted");
    serve(503);
    await expect(registerPush({ ask: false })).resolves.toEqual({ status: "failed" });
    expect(registeredPushToken()).toBeNull();
  });

  it("gives the token up at sign-out, once, and not at all when none was registered", async () => {
    fakePhone("granted");
    const seen = serve();
    await registerPush({ ask: false });
    await forgetPush();
    await forgetPush();
    expect(seen.map((s) => s.key)).toEqual(["PUT /client/push-devices", "POST /client/push-devices/remove"]);
    expect(seen[1]?.body).toEqual({ token: TOKEN });
    await expect(pushState()).resolves.toEqual({ status: "off" });
  });
});

describe("Push on this phone, in Notification settings", () => {
  const client = createStubAuthClient({ delayMs: 0 });

  it("turns push on with the phone's prompt", async () => {
    const phone = fakePhone("undetermined");
    serve();
    await render(
      <AuthProvider client={client}>
        <PushOnThisPhone />
      </AuthProvider>,
    );
    await fireEvent.press(await screen.findByTestId("push-turn-on"));
    expect(await screen.findByText("Alerts you choose below for push come to this phone.")).toBeTruthy();
    expect(phone.asked).toBe(1);
  });

  it("sends a client who said no to the phone's settings", async () => {
    fakePhone("denied");
    await render(
      <AuthProvider client={client}>
        <PushOnThisPhone />
      </AuthProvider>,
    );
    expect(await screen.findByTestId("push-open-settings")).toBeTruthy();
  });

  it("shows nothing where there is no push", async () => {
    setPushDevice({ platform: null, permission: async () => "unsupported", ask: async () => "unsupported", token: async () => TOKEN });
    await render(
      <AuthProvider client={client}>
        <PushOnThisPhone />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.queryByTestId("auth-restoring")).toBeNull());
    expect(screen.queryByTestId("push-on-this-phone")).toBeNull();
  });
});
