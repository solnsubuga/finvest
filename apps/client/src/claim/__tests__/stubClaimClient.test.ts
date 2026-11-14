import { IdentityError } from "@/identity/types";

import { DEMO_CLAIM_FULL_CHECK, DEMO_CLAIM_SELFIE } from "../demo";
import { createStubClaimClient, DEMO_CLAIM_RETAKE_MESSAGE } from "../stubClaimClient";
import { ClaimClient, ClaimError, MAX_PROBLEM_LENGTH } from "../types";

const photo = { uri: "file:///photo.jpg", name: "photo.jpg", mimeType: "image/jpeg" as const, size: 2048 };

function selfieClient() {
  return createStubClaimClient({ phone: DEMO_CLAIM_SELFIE.phone, delayMs: 0 });
}

async function refused(work: Promise<unknown>, code: string) {
  await expect(work).rejects.toMatchObject({ code });
}

/** Reads the claim until the demo's stand-in for staff has decided. */
async function readTwice(client: ClaimClient) {
  await client.getClaim();
  return client.getClaim();
}

describe("the demo claim", () => {
  it("has no claim for a client who signed up in Finvest 2.0", async () => {
    const client = createStubClaimClient({ phone: "+256772123456", delayMs: 0 });
    expect(await client.getClaim()).toBeNull();
    await refused(client.handIn(), "not_found");
  });

  it("takes both photos before handing in, and names what is missing", async () => {
    const client = selfieClient();
    await refused(client.handIn(), "not_found");
    await client.startCheck();
    await client.sendPhoto("front", photo);
    const missing = await client.handIn().catch((caught: ClaimError) => caught);
    expect(missing).toMatchObject({ code: "incomplete", missing: ["selfie"] });
    await client.sendPhoto("selfie", photo);
    expect((await client.handIn()).status).toBe("identity_submitted");
    // Handed in once.
    await refused(client.handIn(), "cannot_change");
    await refused(client.sendPhoto("front", photo), "cannot_change");
  });

  it("asks for the selfie again once, then matches", async () => {
    const client = selfieClient();
    await client.startCheck();
    await client.sendPhoto("front", photo);
    await client.sendPhoto("selfie", photo);
    await client.handIn();
    expect((await readTwice(client))?.status).toBe("identity_submitted");
    const asked = await client.getCheck();
    expect(asked).toMatchObject({ status: "awaiting_client", retakePhotos: ["selfie"], clientMessage: DEMO_CLAIM_RETAKE_MESSAGE });
    // Only the photo asked for, and not before it is taken again.
    await refused(client.sendPhoto("front", photo), "cannot_change");
    await refused(client.sendRetakes(), "incomplete");
    await client.sendPhoto("selfie", photo);
    expect((await client.sendRetakes()).status).toBe("needs_review");
    expect((await readTwice(client))?.status).toBe("identity_confirmed");
    expect((await client.getCheck())?.status).toBe("matched");
  });

  it("confirms or takes a problem only once identity is confirmed, and refuses an empty one", async () => {
    const client = createStubClaimClient({ phone: DEMO_CLAIM_FULL_CHECK.phone, delayMs: 0, submitIdCheck: async () => undefined });
    await refused(client.confirm(), "cannot_change");
    await refused(client.reportProblem("Wrong amount"), "cannot_change");
    await client.handIn();
    await readTwice(client);
    await refused(client.reportProblem("   "), "not_accepted");
    await refused(client.reportProblem("x".repeat(MAX_PROBLEM_LENGTH + 1)), "not_accepted");
    expect((await client.reportProblem("Wrong amount")).status).toBe("problem_reported");
    await refused(client.confirm(), "cannot_change");
  });

  it("hands the full ID check in, and passes on what the check is missing", async () => {
    const submitIdCheck = jest.fn(async () => {
      throw new IdentityError("incomplete", { missing: ["details"] });
    });
    const client = createStubClaimClient({ phone: DEMO_CLAIM_FULL_CHECK.phone, delayMs: 0, submitIdCheck });
    await expect(client.handIn()).rejects.toMatchObject({ code: "incomplete", missing: ["details"] });
    expect((await client.getClaim())?.status).toBe("signed_in");
    submitIdCheck.mockResolvedValueOnce(undefined as never);
    expect((await client.handIn()).status).toBe("identity_submitted");
  });
});
