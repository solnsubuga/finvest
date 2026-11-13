/**
 * The console's view of identity checks.
 *
 * A stand-in for the generated client in packages/api-client, like AuthApi.
 * The shapes follow backend/app/modules/kyc (see docs/identity-checks.md). When
 * the backend publishes its routes, an implementation that calls them replaces
 * the stub in index.tsx and the screen does not change.
 */

export const CHECK_STATUSES = [
  "submitted",
  "needs_review",
  "with_compliance",
  "awaiting_client",
  "approved",
  "rejected",
] as const;
export type CheckStatus = (typeof CHECK_STATUSES)[number];

export type DocumentType = "national_id" | "passport" | "driving_permit" | "refugee_id";
export type PhotoKind = "front" | "back" | "selfie";
export type Verdict = "pass" | "fail" | "unsure";
export type AutomaticCheckName = "document_genuine" | "selfie_match" | "screening";

/** What the signed-in person may do to a check in its current state. The backend decides. */
export type Decision = "approve" | "reject" | "ask_for_photos" | "escalate";

export interface QueueItem {
  id: string;
  /** Null until the client has confirmed their details. */
  name: string | null;
  documentType: DocumentType;
  status: CheckStatus;
  submittedAt: string;
  reviewFlags: readonly string[];
}

export interface Photo {
  kind: PhotoKind;
  /** A short-lived signed link. Null when the photo cannot be shown. */
  url: string | null;
}

export interface AutomaticCheck {
  check: AutomaticCheckName;
  verdict: Verdict;
  /** A whole percentage, when the provider gives one. */
  score: number | null;
}

export interface CheckDetail extends QueueItem {
  documentCountry: string;
  surname: string;
  givenNames: string;
  documentNumber: string;
  dateOfBirth: string;
  documentExpiresOn: string;
  photos: readonly Photo[];
  automaticChecks: readonly AutomaticCheck[];
  /** Photos staff asked the client to take again. */
  retakePhotos: readonly PhotoKind[];
  clientMessage: string | null;
  accountNumber: string | null;
  allowed: readonly Decision[];
}

export interface KycApi {
  /** Checks that are not decided, the ones needing a person first. */
  listQueue(): Promise<QueueItem[]>;
  getCheck(id: string): Promise<CheckDetail>;
  approve(id: string, reason: string): Promise<CheckDetail>;
  reject(id: string, reason: string, clientMessage: string): Promise<CheckDetail>;
  askForPhotos(id: string, photos: readonly PhotoKind[], clientMessage: string): Promise<CheckDetail>;
  escalate(id: string, reason: string): Promise<CheckDetail>;
}

export type KycErrorCode = "not_found" | "conflict" | "duplicate_document" | "forbidden" | "not_connected" | "unavailable";

export class KycError extends Error {
  readonly code: KycErrorCode;

  constructor(code: KycErrorCode) {
    super(code);
    this.name = "KycError";
    this.code = code;
  }
}

const MESSAGES: Record<KycErrorCode, string> = {
  not_found: "This check no longer exists.",
  conflict: "Someone else has already acted on this check. It has been reloaded.",
  duplicate_document: "Another approved client already holds this identity document. Escalate to compliance.",
  forbidden: "You are not allowed to decide identity checks.",
  not_connected: "Identity checks are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function kycErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof KycError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new KycError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedKycApi: KycApi = {
  listQueue: notConnected,
  getCheck: notConnected,
  approve: notConnected,
  reject: notConnected,
  askForPhotos: notConnected,
  escalate: notConnected,
};
