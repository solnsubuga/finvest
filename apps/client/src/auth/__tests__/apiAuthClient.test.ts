import { ApiError, setFetch } from "@/api/http";
import { readToken, saveToken } from "@/api/token";

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
    const key = `${init?.method} ${String(url).replace("https://api.finvestug.com", "")}`;
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
    expect(session).toEqual({ phone: "+256772123456", displayName: null, restricted: false });
    expect(await readToken()).toBe("phone-token");
    await expect(client.restoreSession()).resolves.toEqual(session);
    expect(seen.at(-1)!.auth).toBe("Bearer phone-token");
  });

  it("offers only what a phone can do: no passkey without WebAuthn, and always an SMS code", async () => {
    serve({ "POST /client/login/start": { status: 200, body: { login_id: ID, methods: ["passkey", "authenticator", "code"] } } });
    const challenge = await createApiAuthClient().startLogin("+256772123456", "pw");
    expect(challenge.methods).toEqual(["authenticator", "code"]);
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
