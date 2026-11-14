import type { PhotoFile } from "@/identity/types";

/**
 * The contract between the "Moving to Finvest 2.0" screens and whatever holds
 * a migrated client's claim (docs/migration.md, `/client/claim` and
 * `/client/claim-check`).
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
 * `selfie`: Finvest 1.0 checked the client's ID, so a photo of its front and a
 * selfie are matched to it. `full_id_check`: it did not, so the client takes
 * the full ID check.
 */
export type IdentityStep = "selfie" | "full_id_check";

export type Claim = { status: ClaimStatus; identityStep: IdentityStep };

/** The two photos of a selfie claim check. */
export type ClaimPhotoKind = "front" | "selfie";

export const CLAIM_PHOTOS: readonly ClaimPhotoKind[] = ["front", "selfie"];

/**
 * - `draft`: the client is taking the photos.
 * - `needs_review`: a person is comparing them.
 * - `awaiting_client`: staff asked for named photos again, with a message.
 * - `matched`, `not_matched`: final.
 */
export type ClaimCheckStatus = "draft" | "needs_review" | "awaiting_client" | "matched" | "not_matched";

export type ClaimCheck = {
  id: string;
  status: ClaimCheckStatus;
  /** The kinds of photo handed in so far. */
  photos: ClaimPhotoKind[];
  /** What is still needed before it can be handed in. */
  missing: ClaimPhotoKind[];
  /** The photos staff asked for again. Only while `awaiting_client`. */
  retakePhotos: ClaimPhotoKind[];
  /** Of those, the ones taken again since. */
  retaken: ClaimPhotoKind[];
  /** What staff wrote to the client when asking for photos again. */
  clientMessage: string | null;
};

export type ClaimErrorCode =
  /** Not a client moved from Finvest 1.0, or no claim check started. */
  | "not_found"
  /** The claim or the check has moved on, so this cannot be done now. */
  | "cannot_change"
  /** Something is still needed before handing in. `missing` names it. */
  | "incomplete"
  /** The photo was not accepted: too big, or not a JPEG, PNG or HEIC. */
  | "photo_not_accepted"
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
  /** The signed-in client's claim. Null for a client who signed up in Finvest 2.0. */
  getClaim(): Promise<Claim | null>;
  /** The selfie claim check, if one was started. */
  getCheck(): Promise<ClaimCheck | null>;
  /** Starts the selfie claim check, or answers the one already started. */
  startCheck(): Promise<ClaimCheck>;
  sendPhoto(kind: ClaimPhotoKind, file: PhotoFile): Promise<ClaimCheck>;
  /**
   * Hands the identity step in: the claim check's photos, or the full ID
   * check. Fails with `incomplete` and what is missing, and then nothing changes.
   */
  handIn(): Promise<Claim>;
  /** Sends the photos staff asked for again. */
  sendRetakes(): Promise<ClaimCheck>;
  /** The investments shown are right. Final. */
  confirm(): Promise<Claim>;
  /** Something carried over is wrong. Staff look into it. */
  reportProblem(description: string): Promise<Claim>;
};

const messages: Record<ClaimErrorCode, string> = {
  not_found: "We could not find your Finvest 1.0 account. Write to us in Help and we will sort it out.",
  cannot_change: "This has already moved on. Here is where it stands now.",
  incomplete: "Something is still missing. Take each photo first.",
  photo_not_accepted: "That photo was not accepted. Use a JPEG, PNG or HEIC photo under 10 MB.",
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
