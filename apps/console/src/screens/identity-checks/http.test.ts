import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { kycErrorMessage } from "./api.ts";
import { createHttpKycApi } from "./http.ts";

const CHECK = {
  id: "-NcheckA000000000001",
  name: "David Mugisha",
  document_type: "national_id",
  status: "needs_review",
  submitted_at: "2026-11-13T07:40:00Z",
  review_flags: ["selfie_match_unsure"],
  client_id: "-Nclient00000000000A",
  document_country: "UG",
  surname: "MUGISHA",
  given_names: "David",
  document_number_masked: "CM90••••••••4PL",
  date_of_birth: "1990-03-14",
  document_expires_on: "2031-08-02",
  photos: [
    { id: "-NphotoFront00000001", kind: "front" },
    { id: "-NphotoSelfie0000001", kind: "selfie" },
  ],
  automatic_checks: [{ check: "selfie_match", verdict: "unsure", score: 62 }],
  retake_photos: [],
  client_message: null,
  account_number: null,
  decided_at: null,
  allowed: ["approve", "ask_for_photos", "reject", "escalate"],
};

const LINKS = {
  photos: [{ id: "-NphotoFront00000001", kind: "front", url: "https://files.test/front?sig=1" }],
  expires_at: "2026-11-13T07:45:00Z",
};

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function refusal(status: number, code: string) {
  return reply(status, { request_id: "req", error: { code, message: code, fields: [] } });
}

describe("identity checks over HTTP", () => {
  it("reads the queue without asking for any photos", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, { items: [CHECK] }));
    const [item] = await createHttpKycApi(createHttp("https://api.test", fetch)).listQueue();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/identity-checks");
    expect(item).toEqual({
      id: CHECK.id,
      name: "David Mugisha",
      documentType: "national_id",
      status: "needs_review",
      submittedAt: "2026-11-13T07:40:00Z",
      reviewFlags: ["selfie_match_unsure"],
    });
  });

  it("opens a check with links to its photos, and a photo without one shows by name", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, CHECK))
      .mockImplementationOnce(() => reply(200, LINKS));
    const check = await createHttpKycApi(createHttp("https://api.test", fetch)).getCheck(CHECK.id);
    expect(fetch.mock.calls[1]?.[0]).toBe(`https://api.test/staff/identity-checks/${CHECK.id}/photo-links`);
    expect(fetch.mock.calls[1]?.[1]?.method).toBe("POST");
    expect(check.photos).toEqual([
      { kind: "front", url: "https://files.test/front?sig=1" },
      { kind: "selfie", url: null },
    ]);
    expect(check).toMatchObject({ documentNumberMasked: "CM90••••••••4PL", givenNames: "David", automaticChecks: [{ score: 62 }] });
  });

  it("still opens the check when its photos cannot be linked", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, CHECK))
      .mockImplementationOnce(() => refusal(503, "unavailable"));
    const check = await createHttpKycApi(createHttp("https://api.test", fetch)).getCheck(CHECK.id);
    expect(check.photos.every((photo) => photo.url === null)).toBe(true);
  });

  it("reads the whole document number only when asked", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, { document_number: "CM90123456784PL" }));
    expect(await createHttpKycApi(createHttp("https://api.test", fetch)).showDocumentNumber(CHECK.id)).toBe("CM90123456784PL");
    expect(fetch.mock.calls[0]?.[0]).toBe(`https://api.test/staff/identity-checks/${CHECK.id}/document-number`);
  });

  it("sends each decision to its route with the body the backend takes", async () => {
    const decided = { ...CHECK, status: "awaiting_client", allowed: [], photos: [], retake_photos: ["front"] };
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, decided));
    const api = createHttpKycApi(createHttp("https://api.test", fetch));
    await api.approve(CHECK.id, "Matches");
    await api.reject(CHECK.id, "Not the same person", "");
    await api.askForPhotos(CHECK.id, ["front"], "The front is blurred.");
    const last = await api.escalate(CHECK.id, "Possible duplicate");
    const sent = fetch.mock.calls.map((call) => [String(call[0]).split("/").pop(), JSON.parse(String(call[1]?.body))]);
    expect(sent).toEqual([
      ["approve", { reason: "Matches" }],
      ["reject", { reason: "Not the same person", client_message: null }],
      ["ask-for-photos", { photos: ["front"], client_message: "The front is blurred." }],
      ["escalate", { reason: "Possible duplicate" }],
    ]);
    expect(last).toMatchObject({ status: "awaiting_client", retakePhotos: ["front"] });
  });

  it("turns refusals into the screen's errors", async () => {
    const conflict = vi.fn<typeof globalThis.fetch>(() => refusal(409, "conflict"));
    await expect(createHttpKycApi(createHttp("https://api.test", conflict)).approve(CHECK.id, "x")).rejects.toMatchObject({ code: "conflict" });
    const forbidden = vi.fn<typeof globalThis.fetch>(() => refusal(403, "forbidden"));
    await expect(createHttpKycApi(createHttp("https://api.test", forbidden)).escalate(CHECK.id, "x")).rejects.toMatchObject({
      code: "forbidden",
    });
    const invalid = vi.fn<typeof globalThis.fetch>(() => refusal(422, "validation_failed"));
    const refused = await createHttpKycApi(createHttp("https://api.test", invalid))
      .reject(CHECK.id, "x", "y")
      .catch((caught: unknown) => caught);
    expect(refused).toMatchObject({ code: "invalid" });
    expect(kycErrorMessage(refused)).toMatch(/refused the reason or the message/);
    const offline = vi.fn<typeof globalThis.fetch>(() => Promise.reject(new TypeError("offline")));
    await expect(createHttpKycApi(createHttp("https://api.test", offline)).listQueue()).rejects.toMatchObject({ code: "unavailable" });
  });
});
