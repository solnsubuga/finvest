/**
 * Saving the copy of my data: each way it can fail gets its own words, so only a request that
 * never reached Finvest tells the client to check their connection.
 */
import { saveMyData as saveOnPhone } from "../saveData";
import { saveMyData as saveOnWeb } from "../saveData.web";
import { describeProfileError, ProfileError } from "../types";

jest.mock("@/api/config", () => ({ API_URL: "https://api.example.test", USES_COOKIE: true }));
jest.mock("@/api/token", () => ({ readToken: jest.fn(async () => "token") }));

const mockDownload = jest.fn();
jest.mock("expo-file-system", () => ({
  Paths: { cache: "cache" },
  File: Object.assign(
    function File(this: { uri: string; delete: () => void }) {
      this.uri = "file:///cache/copy.json";
      this.delete = jest.fn();
    },
    { downloadFileAsync: (...args: unknown[]) => mockDownload(...args) },
  ),
}));
const mockShare = jest.fn(async () => undefined);
jest.mock("expo-sharing", () => ({ shareAsync: (...args: unknown[]) => mockShare(...(args as [])) }));

const realFetch = global.fetch;
const clicked: string[] = [];

beforeAll(() => {
  (global as unknown as { document: unknown }).document = {
    body: { appendChild: () => undefined },
    createElement: () => {
      const link = { href: "", download: "", rel: "", click: () => clicked.push(link.download), remove: () => undefined };
      return link;
    },
  };
  URL.createObjectURL = jest.fn(() => "blob:copy");
  URL.revokeObjectURL = jest.fn();
});

afterEach(() => {
  global.fetch = realFetch;
  mockDownload.mockReset();
  mockShare.mockClear();
  clicked.length = 0;
});

const answer = (status: number, code?: string) =>
  new Response(JSON.stringify(code ? { request_id: "r1", error: { code, message: code, fields: [] } } : {}), { status });

async function failure(work: () => Promise<void>): Promise<ProfileError> {
  const error = await work().catch((caught: unknown) => caught);
  expect(error).toBeInstanceOf(ProfileError);
  return error as ProfileError;
}

describe("saving my data in a browser", () => {
  it("downloads the file under its own name, with the session cookie", async () => {
    const fetch = jest.fn(async () => new Response("{}", { status: 200 }));
    global.fetch = fetch as unknown as typeof global.fetch;
    await saveOnWeb("finvest-my-data-2026-11-14.json");
    expect(clicked).toEqual(["finvest-my-data-2026-11-14.json"]);
    expect(fetch).toHaveBeenCalledWith("https://api.example.test/client/data-export", expect.objectContaining({ credentials: "include" }));
  });

  it.each([
    [answer(403, "sign_in_again"), "sign_in_again"],
    [answer(429, "rate_limited"), "rate_limited"],
    [answer(429), "rate_limited"],
    [answer(403, "forbidden"), "refused"],
    [answer(404, "not_found"), "refused"],
    [answer(503), "refused"],
  ])("names a refusal by what it was (%#)", async (response, code) => {
    global.fetch = jest.fn(async () => response) as unknown as typeof global.fetch;
    expect((await failure(() => saveOnWeb("x.json"))).code).toBe(code);
  });

  it("says to check the connection only when nothing came back", async () => {
    global.fetch = jest.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof global.fetch;
    const error = await failure(() => saveOnWeb("x.json"));
    expect(error.code).toBe("unavailable");
    expect(describeProfileError(error)).toMatch(/Check your connection/);
    expect(describeProfileError(new ProfileError("rate_limited"))).not.toMatch(/connection/);
    expect(describeProfileError(new ProfileError("refused"))).not.toMatch(/connection/);
  });
});

describe("saving my data on a phone", () => {
  it("shares the downloaded file, then removes it", async () => {
    mockDownload.mockImplementation(async (_url: string, file: unknown) => file);
    await saveOnPhone("copy.json");
    expect(mockDownload).toHaveBeenCalledWith(
      "https://api.example.test/client/data-export",
      expect.anything(),
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: "Bearer token" }) }),
    );
    expect(mockShare).toHaveBeenCalledTimes(1);
  });

  it.each([
    [answer(403, "sign_in_again"), "sign_in_again"],
    [answer(429, "rate_limited"), "rate_limited"],
    [answer(404, "not_found"), "refused"],
  ])("asks once more for the reason a download was refused (%#)", async (response, code) => {
    mockDownload.mockRejectedValue(new Error("download failed"));
    global.fetch = jest.fn(async () => response) as unknown as typeof global.fetch;
    expect((await failure(() => saveOnPhone("copy.json"))).code).toBe(code);
    expect(mockShare).not.toHaveBeenCalled();
  });

  it("says to check the connection when the phone is offline", async () => {
    mockDownload.mockRejectedValue(new Error("offline"));
    global.fetch = jest.fn(async () => {
      throw new TypeError("Network request failed");
    }) as unknown as typeof global.fetch;
    expect((await failure(() => saveOnPhone("copy.json"))).code).toBe("unavailable");
  });
});
