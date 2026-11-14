import { API_URL } from "@/api/config";
import { ApiError, setFetch } from "@/api/http";
import { readToken, saveToken } from "@/api/token";

import { registerPush, setPushDevice } from "@/push/registration";

import { createApiAuthClient, toAuthError, toSession } from "../apiAuthClient";
import { AuthError } from "../types";

jest.mock("expo-secure-store", () => {
  const store = new Map<string, string>();
  return {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => void store.set(key, value)),
    deleteItemAsync: jest.fn(async (key: string) => void store.delete(key)),
  };
});

const ID = "AAAAAAAAAAAAAAAAAAAA";
const RID = "R".repeat(20);

type Route = { status: number; body?: unknown };

function serve(routes: Record<string, Route | (() => Route)>) {
  const seen: { key: string; body: unknown; auth: string | undefined }[] = [];
  setFetch(async (url, init) => {
    const key = `${init?.method} ${String(url).replace(API_URL, "")}`;
    const headers = (init?.headers ?? {}) as Record<string, string>;
    seen.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null, auth: headers.Authorization });
    const found = routes[key];
    if (!found) throw new TypeError("Network request failed");
    const route = typeof found === "function" ? found() : found;
    return new Response(route.body === undefined ? null : JSON.stringify(route.body), { status: route.status });
  });
  return seen;
}

function refused(status: number, code: string): Route {
  return { status, body: { request_id: RID, error: { code, message: "x", fields: [] } } };
}

const signedIn = (token: string | null, restrictedUntil: string | null = null) => ({
  status: 200,
  body: {
    token,
    idle_expires_at: "2026-11-13T12:00:00Z",
    absolute_expires_at: "2026-11-14T00:00:00Z",
    session: { client_id: ID, phone: "+256772123456", restricted_until: restrictedUntil },
  },
});

afterEach(async () => {
  setFetch(null);
  await saveToken(null);
});

describe("the sign-in client for the real API", () => {
  it("keeps the phone's token after sign up and sends it from then on", async () => {
    const seen = serve({
      "POST /client/signup/start": { status: 200, body: { challenge_id: ID, phone: "+256772123456" } },
      "POST /client/signup/confirm": { status: 200, body: { ticket: "t".repeat(32), phone: "+256772123456" } },
      "POST /client/signup/complete": signedIn("phone-token"),
      "GET /client/session": { status: 200, body: { client_id: ID, phone: "+256772123456", restricted_until: null } },
    });
    const client = createApiAuthClient();
    const challenge = await client.startSignup({ phone: "+256772123456", email: " ", inviteCode: "x" });
    expect(seen[0]!.body).toEqual({ phone: "+256772123456", email: null });
    const ticket = await client.confirmSignupCode(challenge.challengeId, "123456");
    const session = await client.completeSignup(ticket.ticket, "a-long-unusual-phrase");
    expect(session).toEqual({ phone: "+256772123456", displayName: null, restricted: false, email: null, emailVerified: false });
    expect(await readToken()).toBe("phone-token");
    await expect(client.restoreSession()).resolves.toEqual(session);
    expect(seen.at(-1)!.auth).toBe("Bearer phone-token");
  });

  it("keeps the API's list and order, less a passkey on a phone without WebAuthn", async () => {
    serve({ "POST /client/login/start": { status: 200, body: { login_id: ID, methods: ["passkey", "authenticator", "code"] } } });
    const challenge = await createApiAuthClient().startLogin("+256772123456", "pw");
    expect(challenge.methods).toEqual(["authenticator", "code"]);
  });

  it("resets a password with the texted code and forgets this device's token", async () => {
    await saveToken("old-token");
    const seen = serve({
      "POST /client/password/reset/start": { status: 200, body: { challenge_id: ID } },
      "POST /client/password/reset/complete": { status: 204 },
    });
    const client = createApiAuthClient();
    const reset = await client.startPasswordReset("+256772123456");
    expect(reset).toEqual({ challengeId: ID, phone: "+256772123456" });
    await client.completePasswordReset(reset.challengeId, "123456", "a long new phrase");
    expect(seen.map((s) => s.body)).toEqual([
      { phone: "+256772123456" },
      { challenge_id: ID, code: "123456", password: "a long new phrase" },
    ]);
    await expect(readToken()).resolves.toBeNull();
  });

  it("says a wrong reset code is wrong, and keeps the token when nothing changed", async () => {
    await saveToken("old-token");
    serve({ "POST /client/password/reset/complete": refused(422, "invalid_code") });
    await expect(createApiAuthClient().completePasswordReset(ID, "000000", "a long new phrase")).rejects.toEqual(
      new AuthError("invalid_code"),
    );
    await expect(readToken()).resolves.toBe("old-token");
  });

  it("reads the devices and passkeys in the screens' shape", async () => {
    serve({
      "GET /client/sessions": {
        status: 200,
        body: {
          items: [
            { id: ID, device: "Chrome on Android", transport: "cookie", second_step: "code", signed_in_at: "2026-11-13T10:00:00Z", last_seen_at: "2026-11-13T11:00:00Z", current: true },
            { id: "B".repeat(20), device: "Finvest app on iPhone", transport: "token", second_step: "passkey", signed_in_at: "2026-11-10T10:00:00Z", last_seen_at: "2026-11-12T11:00:00Z", current: false },
          ],
        },
      },
      "GET /client/passkeys": {
        status: 200,
        body: [{ id: ID, name: "iPhone", created_at: "2026-11-01T10:00:00Z", last_used_at: null }],
      },
    });
    const client = createApiAuthClient();
    const devices = await client.listDevices();
    expect(devices.map((d) => [d.label, d.kind, d.confirmedWith, d.current])).toEqual([
      ["Chrome on Android", "browser", "code", true],
      ["Finvest app on iPhone", "app", "passkey", false],
    ]);
    await expect(client.listPasskeys()).resolves.toEqual([
      { id: ID, name: "iPhone", createdAt: "2026-11-01T10:00:00Z", lastUsedAt: null },
    ]);
  });

  it("signs out devices and removes sign-in methods on the right routes", async () => {
    const seen = serve({
      [`DELETE /client/sessions/${ID}`]: { status: 204 },
      "POST /client/sessions/sign-out-others": { status: 200, body: { ended: 2 } },
      [`DELETE /client/passkeys/${ID}`]: { status: 204 },
      "DELETE /client/authenticator": { status: 204 },
    });
    const client = createApiAuthClient();
    await client.signOutDevice(ID);
    await expect(client.signOutOtherDevices()).resolves.toBe(2);
    await client.removePasskey(ID);
    await client.turnOffAuthenticator();
    expect(seen.map((s) => s.key)).toEqual([
      `DELETE /client/sessions/${ID}`,
      "POST /client/sessions/sign-out-others",
      `DELETE /client/passkeys/${ID}`,
      "DELETE /client/authenticator",
    ]);
  });

  it("passes on a stale session and a device already gone", async () => {
    serve({
      [`DELETE /client/passkeys/${ID}`]: refused(403, "sign_in_again"),
      [`DELETE /client/sessions/${ID}`]: refused(404, "not_found"),
    });
    const client = createApiAuthClient();
    await expect(client.removePasskey(ID)).rejects.toEqual(new AuthError("sign_in_again"));
    await expect(client.signOutDevice(ID)).rejects.toEqual(new AuthError("not_found"));
  });

  it("reads the email and whether it is proved, and never calls an absent address proved", () => {
    expect(toSession({ phone: "+256772123456", restricted_until: null, email: "a@example.com", email_verified: true })).toMatchObject({
      email: "a@example.com",
      emailVerified: true,
    });
    expect(toSession({ phone: "+256772123456", restricted_until: null, email: null, email_verified: true })).toMatchObject({
      email: null,
      emailVerified: false,
    });
  });

  it("asks for an email link and spends one", async () => {
    const seen = serve({
      "POST /client/email/verification": { status: 200, body: { sent: false } },
      "POST /client/email/verification/confirm": refused(410, "expired"),
    });
    const client = createApiAuthClient();
    await expect(client.sendEmailProof()).resolves.toBe("proved");
    await expect(client.confirmEmail("t".repeat(43))).rejects.toEqual(new AuthError("expired"));
    expect(seen[1]?.body).toEqual({ token: "t".repeat(43) });
  });

  it("gives up this phone's push token before signing out", async () => {
    setPushDevice({ platform: "ios", permission: async () => "granted", ask: async () => "granted", token: async () => "ExponentPushToken[x]" });
    const seen = serve({
      "PUT /client/push-devices": { status: 204 },
      "POST /client/push-devices/remove": { status: 204 },
      "POST /client/sign-out": { status: 204 },
    });
    await registerPush({ ask: false });
    await createApiAuthClient().signOut();
    setPushDevice(null);
    expect(seen.map((s) => s.key)).toEqual(["PUT /client/push-devices", "POST /client/push-devices/remove", "POST /client/sign-out"]);
  });

  it("adds nothing the API did not list", async () => {
    serve({ "GET /client/second-steps": { status: 200, body: { methods: ["authenticator"] } } });
    await expect(createApiAuthClient().listSecondSteps()).resolves.toEqual(["authenticator"]);
  });

  it("marks a session opened with an SMS code as restricted until the API's time", async () => {
    serve({
      "POST /client/login/code/verify": signedIn("t", "2026-11-14T10:00:00Z"),
    });
    const session = await createApiAuthClient().verifyWithCode(ID, "123456");
    expect(session.restricted).toBe(Date.now() < Date.parse("2026-11-14T10:00:00Z"));
    expect(toSession({ phone: "+256", restricted_until: "2026-11-14T10:00:00Z" }, Date.parse("2026-11-14T09:00:00Z")).restricted).toBe(true);
    expect(toSession({ phone: "+256", restricted_until: "2026-11-14T10:00:00Z" }, Date.parse("2026-11-14T11:00:00Z")).restricted).toBe(false);
  });

  it("starts signed out when the stored session has ended, and forgets the token", async () => {
    await saveToken("old");
    serve({ "GET /client/session": refused(401, "unauthorized") });
    await expect(createApiAuthClient().restoreSession()).resolves.toBeNull();
    expect(await readToken()).toBeNull();
  });

  it("signs out on this device even when the API cannot be reached", async () => {
    await saveToken("t");
    serve({});
    await createApiAuthClient().signOut();
    expect(await readToken()).toBeNull();
  });

  it("turns the API's refusals into reasons the screens know", async () => {
    serve({
      "POST /client/login/start": refused(401, "invalid_credentials"),
      "POST /client/signup/confirm": refused(409, "phone_taken"),
      "POST /client/authenticator/options": refused(403, "sign_in_again"),
      "POST /client/authenticator": refused(401, "unauthorized"),
      "POST /client/login/code/send": refused(429, "rate_limited"),
    });
    const client = createApiAuthClient();
    await expect(client.startLogin("+256772123456", "x")).rejects.toEqual(new AuthError("invalid_credentials"));
    await expect(client.confirmSignupCode(ID, "123456")).rejects.toEqual(new AuthError("phone_taken"));
    await expect(client.startAuthenticatorSetup()).rejects.toEqual(new AuthError("sign_in_again"));
    await expect(client.confirmAuthenticatorSetup("123456")).rejects.toEqual(new AuthError("sign_in_again"));
    await expect(client.sendLoginCode(ID)).rejects.toEqual(new AuthError("rate_limited"));
    await expect(client.addPasskey()).rejects.toEqual(new AuthError("passkey_unsupported"));
  });

  it("says offline when nothing could be sent, and unavailable for anything it does not know", () => {
    expect(toAuthError(new ApiError("offline")).code).toBe("offline");
    expect(toAuthError(new ApiError("timeout")).code).toBe("unavailable");
    expect(toAuthError(new ApiError("validation_failed")).code).toBe("not_accepted");
    expect(toAuthError(new Error("boom")).code).toBe("unavailable");
  });
});
