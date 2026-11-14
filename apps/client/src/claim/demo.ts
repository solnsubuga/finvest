/**
 * The demo's accounts moved from Finvest 1.0, shared by the demo sign-in
 * and the demo claim so both answer for the same people. Not real people and
 * not real links: the demo build opens `/claim#<token>` with one of these.
 */
export const DEMO_CLAIM_SELFIE = {
  token: "demo-claim-selfie-link-not-a-real-token",
  phone: "+256772555101",
  displayName: "Grace Namukasa",
} as const;

/** Finvest 1.0 never checked this client's ID, so they take the full ID check. */
export const DEMO_CLAIM_FULL_CHECK = {
  token: "demo-claim-full-check-link-not-a-real-token",
  phone: "+256772555102",
  displayName: "Peter Ssemwanga",
} as const;

/** Their selfie does not match the ID Finvest 1.0 checked, so they are sent to the full ID check. */
export const DEMO_CLAIM_NOT_MATCHED = {
  token: "demo-claim-not-matched-link-not-a-real-token",
  phone: "+256772555103",
  displayName: "Joan Nakato",
} as const;

export const DEMO_CLAIMS = [DEMO_CLAIM_SELFIE, DEMO_CLAIM_FULL_CHECK, DEMO_CLAIM_NOT_MATCHED] as const;
