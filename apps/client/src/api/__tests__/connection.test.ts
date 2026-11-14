import { API_URL } from "@/api/config";
import { currentConnection, onConnection, resetConnection } from "@/api/connection";
import { api, ApiError, setFetch } from "@/api/http";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const ok = [{ code: "TREA-91", name: "91 Day Treasury Bill", kind: "treasury_bill", currency: "UGX", term_days: 91, minimum_minor: 100000, interest_paid: "at_maturity", status: "live", client_rate_percent: "10.63" }];

function answer(status: number, body: unknown, headers: Record<string, string> = {}) {
  setFetch(async (url) => {
    expect(String(url).startsWith(API_URL)).toBe(true);
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers });
  });
}

const read = () => api("GET /client/products", {});

beforeEach(() => resetConnection());
afterEach(() => setFetch(null));

describe("what the app last heard from the API", () => {
  it("is offline when nothing answered, and online again on the next answer", async () => {
    const heard: string[] = [];
    onConnection((connection) => heard.push(connection));
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(read()).rejects.toMatchObject({ code: "offline" });
    expect(currentConnection()).toBe("offline");
    answer(200, ok);
    await read();
    expect(heard).toEqual(["offline", "online"]);
  });

  it("is maintenance when the API says so, and that refusal is certain: nothing was done", async () => {
    answer(503, { request_id: "R".repeat(20), error: { code: "maintenance", message: "x", fields: [] } }, { "Retry-After": "120" });
    const error = await read().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("maintenance");
    expect((error as ApiError).uncertain).toBe(false);
    expect(currentConnection()).toBe("maintenance");
  });

  it("is maintenance for the edge's bare 502 while the API is stopped, which stays uncertain", async () => {
    answer(502, "<html>Bad gateway</html>");
    const error = (await read().catch((caught: unknown) => caught)) as ApiError;
    expect(error.code).toBe("internal_error");
    expect(error.uncertain).toBe(true);
    expect(currentConnection()).toBe("maintenance");
  });

  it("stays online for the API's own refusals and errors", async () => {
    answer(500, { request_id: "R".repeat(20), error: { code: "internal_error", message: "x", fields: [] } });
    await expect(read()).rejects.toMatchObject({ code: "internal_error" });
    expect(currentConnection()).toBe("online");
    answer(503, { request_id: "R".repeat(20), error: { code: "unavailable", message: "x", fields: [] } });
    await expect(read()).rejects.toMatchObject({ code: "unavailable" });
    expect(currentConnection()).toBe("online");
  });
});
