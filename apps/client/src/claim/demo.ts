/**
 * The demo's account moved from paper records, shared by the demo sign-in and
 * the demo claim so both answer for the same person. Not a real person and not
 * a real link: the demo build opens `/claim#<token>` with it.
 */
export const DEMO_CLAIM = {
  token: "demo-claim-link-not-a-real-token",
  phone: "+256772555102",
  displayName: "Peter Ssemwanga",
} as const;

export const DEMO_CLAIMS = [DEMO_CLAIM] as const;
