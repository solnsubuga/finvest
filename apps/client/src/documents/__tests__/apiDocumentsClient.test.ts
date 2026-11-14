import { setFetch } from "@/api/http";

import { createApiDocumentsClient, safeFileName } from "../apiDocumentsClient";
import { DocumentsError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "unlocked-this-device",
  getItemAsync: jest.fn(async () => "session-token"),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

const saved: { path: string; name: string }[] = [];
jest.mock("../savePdf", () => ({
  savePdf: jest.fn(async (path: string, name: string) => void saved.push({ path, name })),
}));

const ID = "Pq30vNa2AAAAAAAAAAAA";
const ITEM = {
  id: ID,
  kind: "requested_statement",
  status: "pending",
  date_from: "2026-08-01",
  date_to: "2026-11-13",
  requested_at: "2026-11-13T12:40:00Z",
  finished_at: null,
  file_name: null,
  byte_size: null,
  not_final_from: null,
  investment_id: null,
  deposit_id: null,
  reference: `ST-${ID}`,
  note: null,
};

type Call = { url: string; init: RequestInit };

function answer(status: number, body: unknown) {
  const calls: Call[] = [];
  setFetch(async (url, init) => {
    calls.push({ url: String(url), init: init ?? {} });
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

afterEach(() => setFetch(null));

it("asks for a statement with the period and the key", async () => {
  const calls = answer(202, ITEM);
  const document = await createApiDocumentsClient().requestStatement("2026-08-01", "2026-11-13", "key-123456789012");
  expect(document).toMatchObject({ id: ID, status: "pending", dateFrom: "2026-08-01", notFinalFrom: null, reference: `ST-${ID}` });
  const call = calls[0]!;
  expect(call.url).toMatch(/\/client\/statements$/);
  expect(call.init.method).toBe("POST");
  expect(JSON.parse(String(call.init.body))).toEqual({ date_from: "2026-08-01", date_to: "2026-11-13" });
  expect((call.init.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-123456789012");
});

it("lists documents and reads one", async () => {
  const ready = { ...ITEM, status: "ready", file_name: "Finvest statement October 2026.pdf", byte_size: 4000 };
  let calls = answer(200, { items: [ready] });
  const [first] = await createApiDocumentsClient().listDocuments();
  expect(first).toMatchObject({ status: "ready", fileName: "Finvest statement October 2026.pdf" });
  expect(calls[0]!.url).toMatch(/\/client\/documents\?limit=100$/);
  calls = answer(200, ready);
  expect((await createApiDocumentsClient().getDocument(ID)).byteSize).toBe(4000);
  expect(calls[0]!.url).toMatch(new RegExp(`/client/documents/${ID}$`));
});

it("turns a refused period into the field's reason", async () => {
  answer(422, {
    request_id: "AAAAAAAAAAAAAAAAAAAA",
    error: {
      code: "validation_failed",
      message: "x",
      fields: [{ field_path: ["body", "date_to"], code: "period_in_future" }],
    },
  });
  const error = await createApiDocumentsClient()
    .requestStatement("2026-11-01", "2026-11-20", "key-123456789012")
    .catch((e: unknown) => e);
  expect(error).toBeInstanceOf(DocumentsError);
  expect(error).toMatchObject({ code: "validation_failed", field: "period_in_future" });
});

it("passes on rate limits, and tells no connection from a refusal or a bad answer", async () => {
  answer(429, { request_id: "AAAAAAAAAAAAAAAAAAAA", error: { code: "rate_limited", message: "x", fields: [] } });
  await expect(createApiDocumentsClient().requestStatement("2026-08-01", "2026-11-13", "k-1234567890123")).rejects.toMatchObject({
    code: "rate_limited",
  });
  setFetch(async () => {
    throw new TypeError("Network request failed");
  });
  await expect(createApiDocumentsClient().listDocuments()).rejects.toMatchObject({ code: "offline" });
  answer(503, { request_id: "AAAAAAAAAAAAAAAAAAAA", error: { code: "unavailable", message: "x", fields: [] } });
  await expect(createApiDocumentsClient().listDocuments()).rejects.toMatchObject({ code: "unavailable" });
  answer(200, { items: [{ ...ITEM, kind: "wallet" }] });
  await expect(createApiDocumentsClient().listDocuments()).rejects.toMatchObject({ code: "unavailable" });
});

it("downloads only a ready document, under a plain name", async () => {
  const client = createApiDocumentsClient();
  const base = {
    id: ID,
    kind: "receipt" as const,
    dateFrom: null,
    dateTo: null,
    requestedAt: "2026-11-13T12:40:00Z",
    finishedAt: "2026-11-13T12:40:05Z",
    byteSize: 1000,
    notFinalFrom: null,
    reference: `RC-${ID}`,
    note: null,
  };
  await expect(client.download({ ...base, status: "pending", fileName: null })).rejects.toMatchObject({ code: "not_found" });
  await client.download({ ...base, status: "ready", fileName: "Finvest receipt RC-Pq30vNa2.pdf" });
  expect(saved).toEqual([{ path: `/client/documents/${ID}/file`, name: "Finvest receipt RC-Pq30vNa2.pdf" }]);
  expect(safeFileName({ ...base, status: "ready", fileName: "../../evil\n.exe" })).toBe(`Finvest document ${ID}.pdf`);
});
