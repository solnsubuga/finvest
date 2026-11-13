/**
 * The web download must use the session the web app really has. On the web
 * `api()` sends no token and lets the browser send the HttpOnly cookie
 * (`USES_COOKIE`), so `savePdf.web.ts` must do the same: the cookie, and no
 * Authorization header, at the same API address.
 */
import { api, setFetch } from "@/api/http";

import { savePdf } from "../savePdf.web";

jest.mock("@/api/config", () => ({ API_URL: "https://api.example.test", USES_COOKIE: true }));

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
  // A token is stored, to prove neither request sends it on the web.
  getItemAsync: jest.fn(async () => "token-that-must-not-be-sent"),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

type Seen = { url: string; init: RequestInit };

const clicked: { href: string; download: string }[] = [];
const realFetch = global.fetch;

beforeAll(() => {
  // The test runs outside a browser: just enough of one for a download link.
  (global as unknown as { document: unknown }).document = {
    body: { appendChild: () => undefined },
    createElement: () => {
      const link = {
        href: "",
        download: "",
        rel: "",
        click: () => clicked.push({ href: link.href, download: link.download }),
        remove: () => undefined,
      };
      return link;
    },
  };
  URL.createObjectURL = jest.fn(() => "blob:statement");
  URL.revokeObjectURL = jest.fn();
});

afterEach(() => {
  global.fetch = realFetch;
  setFetch(null);
  clicked.length = 0;
});

function headersOf(init: RequestInit): Record<string, string> {
  return Object.fromEntries(new Headers(init.headers).entries());
}

it("downloads with the same session as api(): the cookie, never a token", async () => {
  const seen: Seen[] = [];
  const record = async (url: RequestInfo | URL, init?: RequestInit) => {
    seen.push({ url: String(url), init: init ?? {} });
    return String(url).endsWith("/file")
      ? new Response("%PDF-1.7", { status: 200, headers: { "Content-Type": "application/pdf" } })
      : new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  setFetch(record);
  global.fetch = record as typeof fetch;

  await api("GET /client/documents", { query: { limit: 1 } });
  await savePdf("/client/documents/abc/file", "Finvest statement October 2026.pdf");

  const [listed, downloaded] = seen;
  expect(listed!.init.credentials).toBe("include");
  expect(downloaded!.init.credentials).toBe(listed!.init.credentials);
  expect(headersOf(listed!.init).authorization).toBeUndefined();
  expect(headersOf(downloaded!.init).authorization).toBeUndefined();
  expect(downloaded!.url).toBe("https://api.example.test/client/documents/abc/file");
  expect(clicked).toEqual([{ href: "blob:statement", download: "Finvest statement October 2026.pdf" }]);
});

it("refuses an answer that is not a PDF, such as a signed-out error page", async () => {
  global.fetch = (async () =>
    new Response(JSON.stringify({ error: { code: "unauthorized" } }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })) as typeof fetch;
  await expect(savePdf("/client/documents/abc/file", "x.pdf")).rejects.toThrow("download_failed_401");
  global.fetch = (async () =>
    new Response("<html>", { status: 200, headers: { "Content-Type": "text/html" } })) as typeof fetch;
  await expect(savePdf("/client/documents/abc/file", "x.pdf")).rejects.toThrow("download_failed_200");
  expect(clicked).toEqual([]);
});
