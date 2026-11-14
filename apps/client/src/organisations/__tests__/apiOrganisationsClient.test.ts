import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiOrganisationsClient } from "../apiOrganisationsClient";
import { OrganisationError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

afterEach(() => setFetch(null));

type Seen = { method: string; url: string; body: unknown };

function serve(routes: Record<string, () => Response>): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const method = init?.method ?? "GET";
    const path = String(url).slice(API_URL.length);
    const raw = init?.body;
    seen.push({ method, url: path, body: raw instanceof FormData ? raw : raw ? JSON.parse(String(raw)) : undefined });
    const answer = routes[`${method} ${path}`];
    if (!answer) throw new Error(`unexpected ${method} ${path}`);
    return answer();
  });
  return seen;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });
const refused = (code: string, status: number, fields: unknown[] = []) =>
  json({ request_id: "r1", error: { code, message: code, fields } }, status);

const ID = "O".repeat(20);
const view = {
  id: ID,
  kind: "group",
  name: "Kira Road Savings Group",
  registration_number: null,
  approvals_needed: 2,
  status: "draft",
  created_at: "2026-11-14T08:00:00Z",
  submitted_at: null,
  return_note: "Add the resolution's second page.",
  returned_at: "2026-11-14T09:00:00Z",
  decided_at: null,
  account_number: null,
  documents_needed: ["constitution", "group_resolution"],
  documents: [{ kind: "constitution", content_type: "application/pdf", byte_size: 4000, uploaded_at: "2026-11-14T08:10:00Z" }],
  problems: ["documents_missing"],
  signatories: [
    {
      client_id: "C".repeat(20),
      role: "Chairperson",
      name: "Amara N.",
      account_number: "1001004827",
      is_applicant: true,
      status: "joined",
      invited_at: "2026-11-14T08:00:00Z",
      answered_at: "2026-11-14T08:00:00Z",
    },
  ],
  is_applicant: true,
  invited: false,
};

describe("the organisations client for the real API", () => {
  it("applies in the API's words and reads the application back", async () => {
    const seen = serve({ "POST /client/organisations": json(view, 201) });
    const made = await createApiOrganisationsClient().apply({
      kind: "group",
      name: "Kira Road Savings Group",
      registrationNumber: null,
      role: "Chairperson",
      approvalsNeeded: 2,
    });
    expect(seen[0]?.body).toEqual({
      kind: "group",
      name: "Kira Road Savings Group",
      registration_number: null,
      approvals_needed: 2,
      role: "Chairperson",
    });
    expect(made).toMatchObject({
      id: ID,
      returnNote: "Add the resolution's second page.",
      documentsNeeded: ["constitution", "group_resolution"],
      documents: [{ kind: "constitution", contentType: "application/pdf", byteSize: 4000 }],
      signatories: [{ name: "Amara N.", isApplicant: true, status: "joined", accountNumber: "1001004827" }],
    });
  });

  it("sends a document as a form", async () => {
    const seen = serve({ [`PUT /client/organisations/${ID}/documents/group_resolution`]: json(view) });
    await createApiOrganisationsClient().sendDocument(ID, "group_resolution", {
      uri: "file:///minutes.pdf",
      name: "minutes.pdf",
      mimeType: "application/pdf",
      sizeBytes: 4000,
    });
    expect(seen[0]?.body).toBeInstanceOf(FormData);
  });

  it("tells a refused field apart from not being ready", async () => {
    serve({
      [`POST /client/organisations/${ID}/signatories`]: refused("validation_failed", 422, [
        { field_path: ["body", "to"], code: "no_such_client" },
      ]),
      [`POST /client/organisations/${ID}/submission`]: refused("conflict", 409, [
        { field_path: ["problems"], code: "signatories_waiting" },
        { field_path: ["problems"], code: "documents_missing" },
      ]),
    });
    const client = createApiOrganisationsClient();
    await expect(client.invite(ID, "0700000001", "Member")).rejects.toMatchObject({
      code: "invalid",
      fields: [{ field: "to", code: "no_such_client" }],
    });
    await expect(client.submit(ID)).rejects.toMatchObject({ code: "not_ready" });
  });

  it("says when a person's own check is not approved", async () => {
    serve({ "POST /client/organisations": refused("conflict", 409, [{ field_path: ["client"], code: "not_verified" }]) });
    const error = await createApiOrganisationsClient()
      .apply({ kind: "company", name: "X Ltd", registrationNumber: "80020001", role: "Director", approvalsNeeded: 1 })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(OrganisationError);
    expect((error as OrganisationError).code).toBe("not_verified");
  });

  it("treats no answer as unavailable, and a stranger's look as not found", async () => {
    setFetch(async () => {
      throw new TypeError("network");
    });
    await expect(createApiOrganisationsClient().list()).rejects.toMatchObject({ code: "unavailable" });
    serve({ [`GET /client/organisations/${ID}`]: refused("not_found", 404) });
    await expect(createApiOrganisationsClient().get(ID)).rejects.toMatchObject({ code: "not_found" });
  });
});
