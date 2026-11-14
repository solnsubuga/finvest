/**
 * The console's view of identity checks.
 *
 * The shapes follow the staff routes of backend/app/modules/kyc (see
 * docs/identity-checks.md). `http.ts` calls them; `stub.ts` is the sample for
 * `pnpm dev` and previews.
 */

export const CHECK_STATUSES = [
  "submitted",
  "needs_review",
  "with_compliance",
  "awaiting_client",
  "approved",
  "rejected",
  "superseded",
] as const;
export type CheckStatus = (typeof CHECK_STATUSES)[number];

export type DocumentType = "national_id" | "passport" | "driving_permit" | "refugee_id";
export type PhotoKind = "front" | "back" | "selfie";
export type Verdict = "pass" | "fail" | "unsure";
export type AutomaticCheckName = "document_genuine" | "selfie_match" | "screening" | "liveness";

/** Where the lookup of the name the network holds for the client's phone number stands. */
export type PhoneNameStatus = "checking" | "matches" | "partly_matches" | "different" | "no_name" | "not_answered";

export interface PhoneName {
  status: PhoneNameStatus;
  /** The name the network holds for the number, once it gave one. */
  registeredName: string | null;
}

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
  /** True for a selfie captured by a live check that saw a live person. */
  live: boolean;
}

export interface AutomaticCheck {
  check: AutomaticCheckName;
  verdict: Verdict;
  /** A whole percentage, when the provider gives one. */
  score: number | null;
}

export interface CheckDetail extends QueueItem {
  documentCountry: string;
  /** The details the client confirmed. Null until they have. */
  surname: string | null;
  givenNames: string | null;
  /** Masked, such as `CM90••••••••4PL`. The whole number is read with `showDocumentNumber`, which is audited. */
  documentNumberMasked: string | null;
  dateOfBirth: string | null;
  documentExpiresOn: string | null;
  photos: readonly Photo[];
  automaticChecks: readonly AutomaticCheck[];
  /** The network's name for the client's phone number beside the ID name. Null when it was not looked up. */
  phoneName: PhoneName | null;
  /** Photos staff asked the client to take again. */
  retakePhotos: readonly PhotoKind[];
  clientMessage: string | null;
  accountNumber: string | null;
  allowed: readonly Decision[];
}

export interface KycApi {
  /** Checks that are not decided, the ones needing a person first. */
  listQueue(): Promise<QueueItem[]>;
  /** The check, with short-lived links to its photos. Opening the photos is an audit event. */
  getCheck(id: string): Promise<CheckDetail>;
  /** The whole document number. Each call is an audit event. */
  showDocumentNumber(id: string): Promise<string>;
  approve(id: string, reason: string): Promise<CheckDetail>;
  reject(id: string, reason: string, clientMessage: string): Promise<CheckDetail>;
  askForPhotos(id: string, photos: readonly PhotoKind[], clientMessage: string): Promise<CheckDetail>;
  escalate(id: string, reason: string): Promise<CheckDetail>;
}

export type KycErrorCode = "invalid" | "not_found" | "conflict" | "duplicate_document" | "forbidden" | "not_connected" | "not_confirmed" | "unavailable";

export class KycError extends Error {
  readonly code: KycErrorCode;

  constructor(code: KycErrorCode) {
    super(code);
    this.name = "KycError";
    this.code = code;
  }
}

const MESSAGES: Record<KycErrorCode, string> = {
  invalid: "Finvest refused the reason or the message to the client. Check them, keep them short and send again.",
  not_found: "This check no longer exists.",
  conflict: "Someone else has already acted on this check. It has been reloaded.",
  duplicate_document: "Another approved client already holds this identity document. Escalate to compliance.",
  forbidden: "You are not allowed to decide identity checks.",
  not_connected: "Identity checks are not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
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
  showDocumentNumber: notConnected,
  approve: notConnected,
  reject: notConnected,
  askForPhotos: notConnected,
  escalate: notConnected,
};
