import { createStubIdentityClient } from "../stubIdentityClient";
import { IdentityDetails, IdentityError } from "../types";

const NOW = () => Date.parse("2026-11-13T09:00:00Z");
const photo = { uri: "u", name: "p.jpg", mimeType: "image/jpeg" as const, size: 100 };

function details(documentNumber: string): IdentityDetails {
  return { surname: "OKELLO", givenNames: "Peter", documentNumber, dateOfBirth: "1990-01-01", expiresOn: "2030-01-01", tin: null };
}

async function handedIn(documentNumber: string) {
  const client = createStubIdentityClient({ delayMs: 0, now: NOW });
  await client.chooseDocument("national_id", "UG");
  await client.sendPhoto("front", photo);
  await client.sendPhoto("back", photo);
  await client.confirmDetails(details(documentNumber));
  await client.sendPhoto("selfie", photo);
  await client.submit();
  // The first read after handing in still shows it with staff.
  await expect(client.getCheck()).resolves.toMatchObject({ status: "needs_review" });
  return client;
}

describe("the demo identity check", () => {
  it("starts empty, and approved for the seeded demo account", async () => {
    await expect(createStubIdentityClient({ delayMs: 0 }).getCheck()).resolves.toBeNull();
    await expect(createStubIdentityClient({ delayMs: 0, seeded: true }).getCheck()).resolves.toMatchObject({
      status: "approved",
      accountNumber: "1001004827",
    });
  });

  it("refuses what the API refuses", async () => {
    const client = createStubIdentityClient({ delayMs: 0, now: NOW });
    await expect(client.sendPhoto("front", photo)).rejects.toEqual(new IdentityError("not_found"));
    await client.chooseDocument("passport", "UG");
    await expect(client.sendPhoto("back", photo)).rejects.toEqual(new IdentityError("photo_not_needed"));
    await expect(client.submit()).rejects.toMatchObject({ code: "incomplete", missing: ["front", "selfie", "details"] });
    await expect(client.confirmDetails({ ...details("CF94012345"), dateOfBirth: "2010-01-01" })).rejects.toMatchObject({
      code: "invalid_details",
      fields: [{ field: "dateOfBirth", code: "under_age" }],
    });
  });

  it("asks for details again when the document changes", async () => {
    const client = createStubIdentityClient({ delayMs: 0, now: NOW });
    await client.chooseDocument("national_id", "UG");
    await client.confirmDetails(details("CF94012345"));
    const changed = await client.chooseDocument("passport", "KE");
    expect(changed.detailsConfirmed).toBe(false);
    expect(changed.missing).toContain("details");
  });

  it("stays with staff, then stands in for their decision by the number's last digit", async () => {
    const waiting = await handedIn("CF94012345");
    await expect(waiting.getCheck()).resolves.toMatchObject({ status: "needs_review" });
    await expect(waiting.chooseDocument("passport", "UG")).rejects.toEqual(new IdentityError("cannot_change"));

    const approved = await handedIn("CF94012343");
    await expect(approved.getCheck()).resolves.toMatchObject({ status: "approved", accountNumber: expect.stringMatching(/^\d{10}$/) });

    const rejected = await handedIn("CF94012342");
    await expect(rejected.getCheck()).resolves.toMatchObject({ status: "rejected", clientMessage: expect.any(String) });
  });

  it("asks for a new selfie, takes only that photo, and needs it before sending", async () => {
    const client = await handedIn("CF94012341");
    await expect(client.getCheck()).resolves.toMatchObject({ status: "awaiting_client", retakePhotos: ["selfie"] });
    await expect(client.sendPhoto("front", photo)).rejects.toEqual(new IdentityError("photo_not_needed"));
    await expect(client.submit()).rejects.toMatchObject({ code: "incomplete", missing: ["selfie"] });
    await client.sendPhoto("selfie", photo);
    await expect(client.submit()).resolves.toMatchObject({ status: "needs_review", retakePhotos: [] });
    await expect(client.getCheck()).resolves.toMatchObject({ status: "needs_review" });
    await expect(client.getCheck()).resolves.toMatchObject({ status: "approved" });
  });
});

describe("the demo's live selfie", () => {
  it("is offered only when switched on, and its capture becomes the selfie", async () => {
    const off = createStubIdentityClient({ delayMs: 0 });
    await expect(off.getLiveness()).resolves.toMatchObject({ available: false, problem: null });

    const client = createStubIdentityClient({ delayMs: 0, liveness: true, now: NOW });
    await expect(client.getLiveness()).resolves.toMatchObject({ available: true, problem: "no_check" });
    await client.chooseDocument("passport", "UG");
    const started = await client.startLiveness();
    expect(started.status).toBe("starting");
    let state = await client.getLiveness();
    expect(state.latest).toMatchObject({ status: "open" });
    for (let read = 0; read < 3 && state.latest?.status === "open"; read++) state = await client.getLiveness();
    expect(state.latest).toMatchObject({ status: "passed", photoUsed: true, url: null });
    await expect(client.getCheck()).resolves.toMatchObject({ photos: ["selfie"], liveSelfie: true });
    expect(state.attemptsLeft).toBe(9);
  });

  it("replaces a waiting check when another starts", async () => {
    const client = createStubIdentityClient({ delayMs: 0, liveness: true, now: NOW });
    await client.chooseDocument("national_id", "UG");
    const first = await client.startLiveness();
    const second = await client.startLiveness();
    expect(second.id).not.toBe(first.id);
    await expect(client.getLiveness()).resolves.toMatchObject({ latest: { id: second.id } });
  });
});

describe("the demo's Regula liveness", () => {
  const TRANSACTION = "0f6c2b1e-5a4d-4c3b-9a2f-1e0d9c8b7a65";

  it("hands out a Regula tag and answers only once the session is reported", async () => {
    const client = createStubIdentityClient({ delayMs: 0, liveness: true, regula: true, guided: true, now: NOW });
    await client.chooseDocument("national_id", "UG");
    const started = await client.startLiveness();
    let state = await client.getLiveness();
    expect(state.latest).toMatchObject({ status: "open", url: `regula-liveness:${started.id}` });
    for (let read = 0; read < 4; read++) state = await client.getLiveness();
    expect(state.latest).toMatchObject({ status: "open" });

    await client.reportLivenessTransaction(started.id, TRANSACTION);
    // The same session again is harmless; another one is refused.
    await client.reportLivenessTransaction(started.id, TRANSACTION);
    await expect(client.reportLivenessTransaction(started.id, "1f6c2b1e-5a4d-4c3b-9a2f-1e0d9c8b7a65")).rejects.toMatchObject({
      code: "liveness_ended",
    });
    for (let read = 0; read < 3 && state.latest?.status === "open"; read++) state = await client.getLiveness();
    expect(state.latest).toMatchObject({ status: "passed", photoUsed: true });
    await expect(client.reportLivenessTransaction(started.id, TRANSACTION)).rejects.toMatchObject({ code: "liveness_ended" });
  });

  it("refuses a session for an attempt that is not open, or that is not a transaction", async () => {
    const client = createStubIdentityClient({ delayMs: 0, liveness: true, regula: true, now: NOW });
    await client.chooseDocument("national_id", "UG");
    await expect(client.reportLivenessTransaction("demo-liveness-0", TRANSACTION)).rejects.toMatchObject({
      code: "liveness_ended",
    });
    const started = await client.startLiveness();
    await client.getLiveness();
    await expect(client.reportLivenessTransaction(started.id, "not-a-transaction")).rejects.toMatchObject({
      code: "unavailable",
    });
  });
});

describe("the demo's guided selfie", () => {
  async function upToTheTurns() {
    const client = createStubIdentityClient({ delayMs: 0, now: NOW, guided: true });
    await client.chooseDocument("national_id", "UG");
    await client.sendPhoto("front", photo);
    await client.sendPhoto("back", photo);
    await client.confirmDetails(details("CF94012343"));
    return client;
  }

  it("needs the selfie first, then a frame for each pose before handing in", async () => {
    const client = await upToTheTurns();
    await expect(client.sendPose("left", photo)).rejects.toMatchObject({ code: "retake", problems: ["selfie_first"] });
    const afterSelfie = await client.sendPhoto("selfie", photo);
    expect(afterSelfie).toMatchObject({ guidedSelfie: true, missing: ["pose_left", "pose_right", "pose_up", "pose_down"] });
    await client.sendPose("left", photo);
    await client.sendPose("right", photo);
    // The demo refuses the first look up, so the prompt can be seen.
    await expect(client.sendPose("up", photo)).rejects.toMatchObject({ code: "retake", problems: ["turn_more"] });
    await client.sendPose("up", photo);
    await expect(client.submit()).rejects.toMatchObject({ code: "incomplete", missing: ["pose_down"] });
    await expect(client.sendPose("down", photo)).resolves.toMatchObject({ poses: ["left", "right", "up", "down"], missing: [] });
    await expect(client.submit()).resolves.toMatchObject({ status: "needs_review" });
  });

  it("drops the frames when the selfie is taken again", async () => {
    const client = await upToTheTurns();
    await client.sendPhoto("selfie", photo);
    await client.sendPose("left", photo);
    await expect(client.sendPhoto("selfie", photo)).resolves.toMatchObject({ poses: [] });
  });

  it("has no frames when the selfie is not the guided one", async () => {
    const client = createStubIdentityClient({ delayMs: 0, now: NOW });
    await client.chooseDocument("national_id", "UG");
    await expect(client.sendPose("left", photo)).rejects.toEqual(new IdentityError("not_found"));
  });
});
