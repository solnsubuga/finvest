import { describe, expect, it, vi } from "vitest";
import { ApiError, createHttp } from "@/api/http.ts";
import type { PasskeyBrowser } from "@/api/webauthn.ts";
import { AuthError, authErrorMessage } from "./api.ts";
import { createHttpAuthApi, toAuthError } from "./http.ts";

const SESSION = {
  staff_id: "aaaaaaaaaaaaaaaaaaaa",
  name: "Grace Auma",
  role: "admin",
  permissions: ["team.view", "a.permission_this_console_does_not_know"],
};

function respond(status: number, body?: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function envelope(code: string, fields: { field_path: string[]; code: string }[] = []) {
  return { request_id: "bbbbbbbbbbbbbbbbbbbb", error: { code, message: "x", fields } };
}

function api(responses: Response[], passkeys?: PasskeyBrowser) {
  const fetchMock = vi.fn<typeof fetch>();
  for (const response of responses) fetchMock.mockResolvedValueOnce(response);
  return { fetchMock, auth: createHttpAuthApi(createHttp("https://api.test/", fetchMock), passkeys) };
}

describe("the HTTP client", () => {
  it("sends cookies, JSON and the idempotency key, and never caches", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(respond(200, { ok: true }));
    const http = createHttp("https://api.test", fetchMock);
    await http.request("POST", "/staff/x", { a: 1 }, { idempotencyKey: "key-1" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.test/staff/x");
    expect(init?.credentials).toBe("include");
    expect(init?.cache).toBe("no-store");
    expect(init?.body).toBe('{"a":1}');
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-1");
  });

  it("turns the error envelope into an ApiError with its code, fields and Retry-After", async () => {
    const http = createHttp(
      "https://api.test",
      vi.fn<typeof fetch>().mockResolvedValue(
        respond(429, envelope("rate_limited", [{ field_path: ["body"], code: "x" }]), { "Retry-After": "90" }),
      ),
    );
    const error = await http.request("GET", "/x").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "rate_limited", status: 429, retryAfterSeconds: 90 });
    expect((error as ApiError).fieldCode).toBe("x");
  });

  it("reports an unreachable API or a page that is not the envelope as unavailable", async () => {
    const offline = createHttp("https://api.test", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")));
    await expect(offline.request("GET", "/x")).rejects.toMatchObject({ code: "unavailable", status: 0 });
    const edge = createHttp(
      "https://api.test",
      vi.fn<typeof fetch>().mockResolvedValue(new Response("<html>", { status: 502 })),
    );
    await expect(edge.request("GET", "/x")).rejects.toMatchObject({ code: "unavailable", status: 502 });
  });
});

describe("staff sign-in over HTTP", () => {
  it("reads the session, keeping only permissions this console knows", async () => {
    const { auth } = api([respond(200, SESSION)]);
    expect(await auth.getSession()).toEqual({
      staffId: SESSION.staff_id,
      name: "Grace Auma",
      role: "admin",
      permissions: ["team.view"],
    });
  });

  it("treats 401 on the session as nobody signed in", async () => {
    const { auth } = api([respond(401, envelope("unauthorized"))]);
    expect(await auth.getSession()).toBeNull();
  });

  it("signs in with a password then an authenticator code", async () => {
    const { auth, fetchMock } = api([
      respond(200, { login_id: "cccccccccccccccccccc", methods: ["authenticator", "code"] }),
      respond(200, SESSION),
    ]);
    const challenge = await auth.startSignIn("grace@finvestug.com", "a long password");
    expect(challenge).toEqual({ challengeId: "cccccccccccccccccccc", methods: ["authenticator"] });
    await auth.finishSignIn(challenge, { method: "authenticator", code: "123456" });
    expect(fetchMock.mock.calls[1]![0]).toBe("https://api.test/staff/login/authenticator/verify");
    expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body))).toEqual({
      login_id: "cccccccccccccccccccc",
      code: "123456",
    });
  });

  it("signs in with a passkey through the browser", async () => {
    const passkeys: PasskeyBrowser = {
      create: vi.fn(),
      get: vi.fn().mockResolvedValue({ id: "cred" }),
    };
    const { auth, fetchMock } = api(
      [respond(200, { public_key: { challenge: "AAAA" } }), respond(200, SESSION)],
      passkeys,
    );
    await auth.finishSignIn({ challengeId: "cccccccccccccccccccc", methods: ["passkey"] }, { method: "passkey" });
    expect(passkeys.get).toHaveBeenCalledWith({ challenge: "AAAA" });
    expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body))).toEqual({
      login_id: "cccccccccccccccccccc",
      credential: { id: "cred" },
    });
  });

  it("enrols with an authenticator app", async () => {
    const { auth } = api([respond(200, { secret: "SECRET", uri: "otpauth://x" }), respond(200, SESSION)]);
    expect(await auth.startAuthenticatorEnrolment("t".repeat(40))).toEqual({ secret: "SECRET", uri: "otpauth://x" });
    expect((await auth.enrolWithAuthenticator("t".repeat(40), "a long password", "123456")).name).toBe("Grace Auma");
  });
});

describe("what the API's refusals are called on the sign-in screens", () => {
  it.each([
    [new ApiError("invalid_credentials", 401), "invalid_credentials"],
    [new ApiError("invalid_code", 401), "invalid_code"],
    [new ApiError("passkey_failed", 401), "passkey_failed"],
    [new ApiError("second_step_missing", 403), "second_step_missing"],
    [new ApiError("expired", 410), "link_expired"],
    [new ApiError("not_found", 404), "link_expired"],
    [new ApiError("leaked_password", 422), "password_leaked"],
    [new ApiError("validation_failed", 422, [{ field_path: ["body", "password"], code: "too_short" }]), "password_too_short"],
    [new ApiError("validation_failed", 422, [{ field_path: ["body", "password"], code: "too_long" }]), "password_too_long"],
    [new ApiError("unavailable", 503), "unavailable"],
    [new ApiError("internal_error", 500), "unavailable"],
  ])("%s becomes %s", (error, code) => {
    expect(toAuthError(error).code).toBe(code);
  });

  it("says how long to wait when the API is rate limiting", () => {
    const error = toAuthError(new ApiError("rate_limited", 429, [], 240));
    expect(error).toBeInstanceOf(AuthError);
    expect(authErrorMessage(error)).toBe("Too many attempts. Try again in 4 minutes.");
  });
});
