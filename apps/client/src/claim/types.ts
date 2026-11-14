/**
 * The contract between the "Opening your account" screens and whatever holds
 * a migrated client's claim (docs/migration.md, `/client/claim`).
 *
 * `apiClaimClient.ts` answers it from the real API. The in-memory demo in
 * `stubClaimClient.ts` answers it in tests and in a demo build.
 */

/**
 * - `signed_in`: the account is theirs. Next, the identity step.
 * - `identity_submitted`: a person is checking it.
 * - `identity_confirmed`: next, the client checks the investments carried over.
 * - `confirmed`: final.
 * - `problem_reported`: the client said something carried over is wrong. Staff look into it.
 */
export type ClaimStatus =
  | "not_invited"
  | "invited"
  | "signed_in"
  | "identity_submitted"
  | "identity_confirmed"
  | "confirmed"
  | "problem_reported";

/**
 * Every client moved from paper records takes the full ID check (the owner,
 * 2026-11-14). The API still names the step, so the type keeps both values.
 */
export type IdentityStep = "selfie" | "full_id_check";

export type Claim = { status: ClaimStatus; identityStep: IdentityStep };

export type ClaimErrorCode =
  /** Not a client whose account was moved from paper records. */
  | "not_found"
  /** The claim or the check has moved on, so this cannot be done now. */
  | "cannot_change"
  /** Something is still needed before handing in. `missing` names it. */
  | "incomplete"
  /** The description is empty or too long. */
  | "not_accepted"
  | "offline"
  | "unavailable";

export class ClaimError extends Error {
  readonly code: ClaimErrorCode;
  readonly missing: string[];

  constructor(code: ClaimErrorCode, missing: string[] = []) {
    super(code);
    this.name = "ClaimError";
    this.code = code;
    this.missing = missing;
  }
}

/** The longest description of a problem the API takes. */
export const MAX_PROBLEM_LENGTH = 1000;

export type ClaimClient = {
  /** The signed-in client's claim. Null for a client who signed up in the app. */
  getClaim(): Promise<Claim | null>;
  /**
   * Hands the full ID check in with the claim. Fails with `incomplete` and
   * what is missing, and then nothing changes.
   */
  handIn(): Promise<Claim>;
  /** The investments shown are right. Final. */
  confirm(): Promise<Claim>;
  /** Something carried over is wrong. Staff look into it. */
  reportProblem(description: string): Promise<Claim>;
};

const messages: Record<ClaimErrorCode, string> = {
  not_found: "We could not find the account we moved for you. Write to us in Help and we will sort it out.",
  cannot_change: "This has already moved on. Here is where it stands now.",
  incomplete: "Something is still missing. Finish each step of the ID check first.",
  not_accepted: `Say what is wrong in a few words, up to ${MAX_PROBLEM_LENGTH} characters.`,
  offline: "You seem to be offline. Check your connection and try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

/** A sentence a client can act on. Never shows codes or technical detail. */
export function describeClaimError(error: unknown): string {
  return messages[error instanceof ClaimError ? error.code : "unavailable"];
}

/** True while the client still has something to do or to wait for. Home shows it. */
export function claimOpen(claim: Claim | null): claim is Claim {
  return claim !== null && claim.status !== "confirmed" && claim.status !== "not_invited" && claim.status !== "invited";
}
