import { api, ApiError, onUnauthorized, setFetch } from "../http";
import { saveToken } from "../token";

jest.mock("expo-secure-store", () => {
  const store = new Map<string, string>();
  return {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => void store.set(key, value)),
    deleteItemAsync: jest.fn(async (key: string) => void store.delete(key)),
  };
});

type Call = { url: string; init: RequestInit };

function answer(status: number, body: unknown, headers: Record<string, string> = {}) {
  const calls: Call[] = [];
  setFetch(async (url, init) => {
    calls.push({ url: String(url), init: init ?? {} });
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    });
  });
  return calls;
}

const ID = "AAAAAAAAAAAAAAAAAAAA";
const payment = {
  id: ID,
  investment_id: ID,
  method: "mtn",
  status: "sending",
  amount: { minor: 500000, currency: "UGX" },
  failure: null,
  payer: "+256772123456",
  receipt: null,
  created_at: "2026-11-13T10:00:00Z",
  confirmed_at: null,
};

afterEach(async () => {
  setFetch(null);
  await saveToken(null);
  jest.useRealTimers();
});

describe("api", () => {
  it("fills in the path, sends the idempotency key and the phone's token, and checks the answer", async () => {
    await saveToken("token-1");
    const calls = answer(201, payment);
    const result = await api("POST /client/investments/{investment_id}/payments/mobile-money", {
      params: { investment_id: "a/b" },
      body: { network: "mtn" },
      idempotencyKey: "key-1",
    });
    expect(result.amount).toEqual({ minor: 500000, currency: "UGX" });
    expect(calls[0]!.url).toBe("https://api.finvestug.com/client/investments/a%2Fb/payments/mobile-money");
    const headers = calls[0]!.init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("key-1");
    expect(headers.Authorization).toBe("Bearer token-1");
    expect(calls[0]!.init.body).toBe(JSON.stringify({ network: "mtn" }));
    expect(calls[0]!.init.credentials).toBe("omit");
  });

  it("leaves out query values that are not set", async () => {
    const calls = answer(200, { currency: "UGX", months: [], invested_now_minor: 0, earned_so_far_minor: 0, upcoming: [] });
    await api("GET /client/insights", { query: { months: 6 } });
    await api("GET /client/insights", {});
    expect(calls.map((call) => call.url)).toEqual([
      "https://api.finvestug.com/client/insights?months=6",
      "https://api.finvestug.com/client/insights",
    ]);
  });

  it("hands back nothing for an answer with no body", async () => {
    answer(204, undefined);
    await expect(api("POST /client/sign-out", {})).resolves.toBeNull();
  });

  it("carries the API's error code, request ID and field problems", async () => {
    answer(422, {
      request_id: "R".repeat(20),
      error: { code: "leaked_password", message: "x", fields: [{ field_path: ["body", "password"], code: "leaked" }] },
    });
    const error = await api("POST /client/signup/complete", { body: { ticket: "t".repeat(20), password: "p" } }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      code: "leaked_password",
      status: 422,
      requestId: "R".repeat(20),
      fields: [{ field: "body.password", code: "leaked" }],
    });
  });

  it("treats a refusal it cannot read as an outage, and a server error as uncertain", async () => {
    setFetch(async () => new Response("<html>bad gateway</html>", { status: 502 }));
    const gateway = (await api("GET /client/session", {}).catch((e: unknown) => e)) as ApiError;
    expect(gateway.code).toBe("internal_error");
    expect(gateway.uncertain).toBe(true);

    setFetch(async () => new Response(JSON.stringify({ error: { code: "something_new" } }), { status: 400 }));
    const unknown = (await api("GET /client/session", {}).catch((e: unknown) => e)) as ApiError;
    expect(unknown.code).toBe("unavailable");
  });

  it("refuses an answer that does not match the contract rather than show part of it", async () => {
    answer(201, { ...payment, amount: { minor: "500000", currency: "UGX" } });
    const error = (await api("POST /client/investments/{investment_id}/payments/mobile-money", {
      params: { investment_id: ID },
      body: { network: "mtn" },
      idempotencyKey: "key-1",
    }).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("bad_response");
    expect(error.uncertain).toBe(true);
  });

  it("says offline when nothing could be sent", async () => {
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    const error = (await api("GET /client/session", {}).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("offline");
    expect(error.uncertain).toBe(false);
  });

  it("says timeout, which may have reached the API, when no answer comes in time", async () => {
    jest.useFakeTimers();
    setFetch(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const pending = api("GET /client/session", { timeoutMs: 1000 }).catch((e: unknown) => e);
    await jest.advanceTimersByTimeAsync(1001);
    const error = (await pending) as ApiError;
    expect(error.code).toBe("timeout");
    expect(error.uncertain).toBe(true);
  });

  it("tells the app when the session has ended", async () => {
    const heard: string[] = [];
    const stop = onUnauthorized((error) => heard.push(error.code));
    answer(401, { request_id: "R".repeat(20), error: { code: "unauthorized", message: "x", fields: [] } });
    await api("GET /client/session", {}).catch(() => {});
    answer(401, { request_id: "R".repeat(20), error: { code: "invalid_code", message: "x", fields: [] } });
    await api("POST /client/login/code/verify", { body: { login_id: ID, code: "123456" } }).catch(() => {});
    stop();
    expect(heard).toEqual(["unauthorized"]);
  });
});
