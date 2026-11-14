import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiIdentityClient } from "../apiIdentityClient";
import { IdentityError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const ID = "KKKKKKKKKKKKKKKKKKKK";

function view(overrides: Record<string, unknown> = {}) {
  return {
    id: ID,
    status: "draft",
    document_type: "national_id",
    document_country: "UG",
    details: null,
    details_confirmed: false,
    photos: [],
    missing: ["front", "back", "details", "selfie"],
    retake_photos: [],
    retaken: [],
    client_message: null,
    submitted_at: null,
    account_number: null,
    ...overrides,
  };
}

type Seen = { method: string; path: string; body: unknown };

function serve(status: number, body: unknown): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    const raw = init?.body;
    seen.push({
      method: String(init?.method),
      path: String(url).replace(API_URL, ""),
      body: raw instanceof FormData ? raw : raw ? JSON.parse(String(raw)) : null,
    });
    return new Response(JSON.stringify(body), { status });
  });
  return seen;
}

function refusal(code: string, fields: { field_path: string[]; code: string }[] = []) {
  return { request_id: "R".repeat(20), error: { code, message: "x", fields } };
}

afterEach(() => setFetch(null));

describe("the identity check client for the real API", () => {
  it("reads no check before a document is chosen", async () => {
    const seen = serve(200, { check: null });
    await expect(createApiIdentityClient().getCheck()).resolves.toBeNull();
    expect(seen[0]).toMatchObject({ method: "GET", path: "/client/identity-check" });
  });

  it("reads a check in the app's terms, account number included", async () => {
    serve(200, {
      check: view({
        status: "approved",
        details: {
          surname: "NAKATO",
          given_names: "Amara",
          document_number: "CF94012345A2KX",
          date_of_birth: "1994-06-22",
          document_expires_on: "2030-09-18",
          tin: null,
        },
        details_confirmed: true,
        photos: [{ kind: "front", content_type: "image/jpeg", byte_size: 10 }],
        missing: [],
        account_number: "1001004827",
      }),
    });
    await expect(createApiIdentityClient().getCheck()).resolves.toMatchObject({
      status: "approved",
      documentType: "national_id",
      details: { givenNames: "Amara", expiresOn: "2030-09-18", tin: null },
      photos: ["front"],
      accountNumber: "1001004827",
    });
  });

  it("chooses a document, confirms details and hands the check in", async () => {
    const client = createApiIdentityClient();
    let seen = serve(200, view());
    await client.chooseDocument("passport", "KE");
    expect(seen[0]).toMatchObject({
      method: "PUT",
      path: "/client/identity-check/document",
      body: { document_type: "passport", document_country: "KE" },
    });

    seen = serve(200, view({ details_confirmed: true }));
    await client.confirmDetails({
      surname: "NAKATO",
      givenNames: "Amara",
      documentNumber: "CF94012345A2KX",
      dateOfBirth: "1994-06-22",
      expiresOn: "2030-09-18",
      tin: "1000123456",
    });
    expect(seen[0]!.body).toEqual({
      surname: "NAKATO",
      given_names: "Amara",
      document_number: "CF94012345A2KX",
      date_of_birth: "1994-06-22",
      document_expires_on: "2030-09-18",
      tin: "1000123456",
    });

    seen = serve(200, view({ status: "needs_review", missing: [] }));
    await expect(client.submit()).resolves.toMatchObject({ status: "needs_review" });
    expect(seen[0]).toMatchObject({ method: "POST", path: "/client/identity-check/submit" });
  });

  it("sends a photo as a form with one field named photo", async () => {
    const seen = serve(200, view({ photos: [{ kind: "selfie", content_type: "image/jpeg", byte_size: 4 }] }));
    await createApiIdentityClient().sendPhoto("selfie", { uri: "file:///s.jpg", name: "s.jpg", mimeType: "image/jpeg", size: 4 });
    expect(seen[0]).toMatchObject({ method: "PUT", path: "/client/identity-check/photos/selfie" });
    const form = seen[0]!.body as FormData;
    expect(form.get("photo")).toBeTruthy();
  });

  it("turns refusals into the reasons the screens show", async () => {
    const client = createApiIdentityClient();
    const photo = { uri: "file:///b.jpg", name: "b.jpg", mimeType: "image/jpeg" as const, size: 4 };

    // During a retake, a photo staff did not ask for is a 409.
    serve(409, refusal("conflict"));
    await expect(client.sendPhoto("front", photo)).rejects.toEqual(new IdentityError("photo_not_needed"));
    await expect(client.submit()).rejects.toEqual(new IdentityError("cannot_change"));

    serve(422, refusal("validation_failed", [{ field_path: ["kind"], code: "photo_not_part_of_document" }]));
    await expect(client.sendPhoto("back", photo)).rejects.toEqual(new IdentityError("photo_not_needed"));

    serve(422, refusal("validation_failed", [{ field_path: ["photo"], code: "not_an_image_of_that_type" }]));
    await expect(client.sendPhoto("back", photo)).rejects.toEqual(new IdentityError("photo_not_accepted"));

    serve(413, refusal("payload_too_large"));
    await expect(client.sendPhoto("back", photo)).rejects.toEqual(new IdentityError("photo_not_accepted"));

    serve(422, refusal("validation_failed", [
      { field_path: ["date_of_birth"], code: "under_age" },
      { field_path: ["document_expires_on"], code: "document_expired" },
    ]));
    const refused = await client
      .confirmDetails({ surname: "A", givenNames: "B", documentNumber: "CF940", dateOfBirth: "2015-01-01", expiresOn: "2020-01-01", tin: null })
      .catch((error: unknown) => error);
    expect(refused).toMatchObject({
      code: "invalid_details",
      fields: [
        { field: "dateOfBirth", code: "under_age" },
        { field: "expiresOn", code: "document_expired" },
      ],
    });

    serve(422, refusal("validation_failed", [{ field_path: ["selfie"], code: "missing" }]));
    await expect(client.submit()).rejects.toMatchObject({ code: "incomplete", missing: ["selfie"] });

    serve(404, refusal("not_found"));
    await expect(client.sendPhoto("front", photo)).rejects.toEqual(new IdentityError("not_found"));

    serve(503, refusal("unavailable"));
    await expect(client.getCheck()).rejects.toEqual(new IdentityError("unavailable"));

    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(client.getCheck()).rejects.toEqual(new IdentityError("offline"));
  });
});

describe("the guided selfie through the real API", () => {
  const frame = { uri: "file:///p.jpg", name: "pose-left.jpg", mimeType: "image/jpeg" as const, size: 4 };

  it("reads whether the selfie is guided, and the poses kept", async () => {
    serve(200, { check: view({ guided_selfie: true, poses: ["left"], missing: ["pose_right", "pose_up", "pose_down"] }) });
    await expect(createApiIdentityClient().getCheck()).resolves.toMatchObject({
      guidedSelfie: true,
      poses: ["left"],
      missing: ["pose_right", "pose_up", "pose_down"],
    });
    // An API from before the guided selfie: not guided.
    serve(200, { check: view() });
    await expect(createApiIdentityClient().getCheck()).resolves.toMatchObject({ guidedSelfie: false, poses: [] });
  });

  it("sends a frame as a form with one field named photo", async () => {
    const seen = serve(200, view({ guided_selfie: true, poses: ["left"] }));
    await createApiIdentityClient().sendPose("left", frame);
    expect(seen[0]).toMatchObject({ method: "PUT", path: "/client/identity-check/poses/left" });
    expect((seen[0]!.body as FormData).get("photo")).toBeTruthy();
  });

  it("turns a refused frame or selfie into the prompts to take it again", async () => {
    const client = createApiIdentityClient();
    serve(422, refusal("validation_failed", [
      { field_path: ["frame"], code: "turn_more" },
      { field_path: ["frame"], code: "too_dark" },
    ]));
    await expect(client.sendPose("up", frame)).rejects.toMatchObject({ code: "retake", problems: ["turn_more", "too_dark"] });

    serve(422, refusal("validation_failed", [{ field_path: ["photo"], code: "look_straight" }]));
    await expect(client.sendPhoto("selfie", frame)).rejects.toMatchObject({ code: "retake", problems: ["look_straight"] });

    serve(422, refusal("validation_failed", [{ field_path: ["pose"], code: "selfie_first" }]));
    await expect(client.sendPose("left", frame)).rejects.toMatchObject({ code: "retake", problems: ["selfie_first"] });

    // A code the app does not know is not a prompt.
    serve(422, refusal("validation_failed", [{ field_path: ["frame"], code: "not_an_image_of_that_type" }]));
    await expect(client.sendPose("left", frame)).rejects.toEqual(new IdentityError("photo_not_accepted"));

    serve(409, refusal("conflict"));
    await expect(client.sendPose("left", frame)).rejects.toEqual(new IdentityError("cannot_change"));

    // The face quality service did not answer: send it again.
    serve(503, refusal("unavailable"));
    await expect(client.sendPose("left", frame)).rejects.toEqual(new IdentityError("unavailable"));
  });
});

describe("the live selfie through the real API", () => {
  it("reads whether a live check is offered and how the latest one stands", async () => {
    const seen = serve(200, {
      available: true,
      problem: null,
      attempts_left: 9,
      latest: {
        id: ID,
        status: "open",
        url: "https://faces.iotec.io/auth?token=t",
        failure_code: null,
        photo_used: false,
        expires_at: "2026-11-14T09:30:00Z",
      },
    });
    await expect(createApiIdentityClient().getLiveness()).resolves.toEqual({
      available: true,
      problem: null,
      attemptsLeft: 9,
      latest: {
        id: ID,
        status: "open",
        url: "https://faces.iotec.io/auth?token=t",
        failureCode: null,
        photoUsed: false,
        expiresAt: "2026-11-14T09:30:00Z",
      },
    });
    expect(seen[0]).toMatchObject({ method: "GET", path: "/client/identity-check/liveness" });
  });

  it("says when every live check is used up, and marks a live selfie", async () => {
    serve(422, refusal("validation_failed", [{ field_path: ["body", "liveness"], code: "too_many_attempts" }]));
    await expect(createApiIdentityClient().startLiveness()).rejects.toMatchObject({ code: "liveness_used_up" });
    serve(409, refusal("conflict"));
    await expect(createApiIdentityClient().startLiveness()).rejects.toMatchObject({ code: "cannot_change" });

    serve(200, {
      check: view({
        photos: [
          { kind: "front", content_type: "image/jpeg", byte_size: 10, live: false },
          { kind: "selfie", content_type: "image/jpeg", byte_size: 10, live: true },
        ],
      }),
    });
    await expect(createApiIdentityClient().getCheck()).resolves.toMatchObject({ liveSelfie: true });
  });
});

describe("Regula's liveness through the real API", () => {
  const TRANSACTION = "0f6c2b1e-5a4d-4c3b-9a2f-1e0d9c8b7a65";
  const attempt = {
    id: ID,
    status: "open",
    url: `regula-liveness:${ID}`,
    failure_code: null,
    photo_used: false,
    expires_at: "2026-11-14T09:30:00Z",
  };

  it("reports the session's transaction for the attempt", async () => {
    const seen = serve(200, attempt);
    await expect(createApiIdentityClient().reportLivenessTransaction(ID, TRANSACTION)).resolves.toMatchObject({
      id: ID,
      status: "open",
      url: `regula-liveness:${ID}`,
    });
    expect(seen[0]).toMatchObject({
      method: "POST",
      path: `/client/identity-check/liveness/${ID}/transaction`,
      body: { transaction_id: TRANSACTION },
    });
  });

  it("says the live check ended when the attempt is gone or took another session", async () => {
    serve(409, refusal("conflict"));
    await expect(createApiIdentityClient().reportLivenessTransaction(ID, TRANSACTION)).rejects.toMatchObject({
      code: "liveness_ended",
    });
    serve(404, refusal("not_found"));
    await expect(createApiIdentityClient().reportLivenessTransaction(ID, TRANSACTION)).rejects.toMatchObject({
      code: "liveness_ended",
    });
    serve(503, refusal("unavailable"));
    await expect(createApiIdentityClient().reportLivenessTransaction(ID, TRANSACTION)).rejects.toMatchObject({
      code: "unavailable",
    });
  });
});
