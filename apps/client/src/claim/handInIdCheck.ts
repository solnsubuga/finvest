import { IdentityCheck, IdentityClient } from "@/identity/types";

import { ClaimClient } from "./types";

/**
 * Hands the ID check in. A client moved from Finvest 1.0 who takes the full
 * check hands it in with their claim, in one step on the server, so the claim
 * moves on with it and staff's decision reaches it. Anyone else hands the
 * check in on its own.
 */
export async function handInIdCheck(identity: IdentityClient, claims: ClaimClient): Promise<IdentityCheck | null> {
  const claim = await claims.getClaim();
  if (claim?.status === "signed_in" && claim.identityStep === "full_id_check") {
    await claims.handIn();
    return identity.getCheck();
  }
  return identity.submit();
}
