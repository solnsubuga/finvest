import { IdentityError } from "@/identity/types";

import { DEMO_CLAIM } from "../demo";
import { createStubClaimClient } from "../stubClaimClient";
import { ClaimClient, MAX_PROBLEM_LENGTH } from "../types";

async function refused(work: Promise<unknown>, code: string) {
  await expect(work).rejects.toMatchObject({ code });
}

/** Reads the claim until the demo's stand-in for staff has decided. */
async function readTwice(client: ClaimClient) {
  await client.getClaim();
  return client.getClaim();
}

describe("the demo claim", () => {
  it("has no claim for a client who signed up in the app", async () => {
    const client = createStubClaimClient({ phone: "+256772123456", delayMs: 0 });
    expect(await client.getClaim()).toBeNull();
    await refused(client.handIn(), "not_found");
  });

  it("hands the ID check in once, and approves it after it was read twice", async () => {
    const client = createStubClaimClient({ phone: DEMO_CLAIM.phone, delayMs: 0, submitIdCheck: async () => undefined });
    expect(await client.getClaim()).toEqual({ status: "signed_in", identityStep: "full_id_check" });
    expect((await client.handIn()).status).toBe("identity_submitted");
    await refused(client.handIn(), "cannot_change");
    expect((await client.getClaim())?.status).toBe("identity_submitted");
    expect((await client.getClaim())?.status).toBe("identity_confirmed");
  });

  it("confirms or takes a problem only once identity is confirmed, and refuses an empty one", async () => {
    const client = createStubClaimClient({ phone: DEMO_CLAIM.phone, delayMs: 0, submitIdCheck: async () => undefined });
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
    const client = createStubClaimClient({ phone: DEMO_CLAIM.phone, delayMs: 0, submitIdCheck });
    await expect(client.handIn()).rejects.toMatchObject({ code: "incomplete", missing: ["details"] });
    expect((await client.getClaim())?.status).toBe("signed_in");
    submitIdCheck.mockResolvedValueOnce(undefined as never);
    expect((await client.handIn()).status).toBe("identity_submitted");
  });
});
