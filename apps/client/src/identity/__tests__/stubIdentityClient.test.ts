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
