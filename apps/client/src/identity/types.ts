/**
 * The contract between the ID check screens and whatever holds the client's
 * identity check (docs/identity-checks.md).
 *
 * `apiIdentityClient.ts` answers it from the real API (`/client/identity-check`).
 * The in-memory demo in `stubIdentityClient.ts` answers it in tests and in a demo build.
 *
 * The client sees where their check stands and what to do next. Never the
 * provider's answers, review flags or who decided.
 */

export type DocumentType = "national_id" | "passport" | "driving_permit" | "refugee_id";

export type PhotoKind = "front" | "back" | "selfie";

/**
 * - `draft`: the client is adding photos and details.
 * - `submitted`, `needs_review`, `with_compliance`: being checked. The client
 *   sees these three the same way.
 * - `awaiting_client`: staff asked for named photos again, with a message.
 * - `approved`: final. The account number is issued.
 * - `rejected`: final.
 */
export type CheckStatus =
  | "draft"
  | "submitted"
  | "needs_review"
  | "with_compliance"
  | "awaiting_client"
  | "approved"
  | "rejected";

/** What the client confirmed, as printed on the document. Dates are "YYYY-MM-DD". */
export type IdentityDetails = {
  surname: string;
  givenNames: string;
  documentNumber: string;
  dateOfBirth: string;
  expiresOn: string;
  tin: string | null;
};

export type IdentityCheck = {
  id: string;
  status: CheckStatus;
  documentType: DocumentType;
  /** ISO 3166 two-letter code, such as "UG". */
  documentCountry: string;
  details: IdentityDetails | null;
  detailsConfirmed: boolean;
  /** The kinds of photo handed in so far. */
  photos: PhotoKind[];
  /** What is still needed before it can be handed in: photo kinds and "details". */
  missing: string[];
  /** The photos staff asked for again. Only while `awaiting_client`. */
  retakePhotos: PhotoKind[];
  /** What staff wrote to the client when asking for photos, or when rejecting. */
  clientMessage: string | null;
  submittedAt: string | null;
  /** The 10-digit Finvest account number, once approved. */
  accountNumber: string | null;
};

export type PhotoMimeType = "image/jpeg" | "image/png" | "image/webp";

/** A photo the device handed us, checked for type and size. */
export type PhotoFile = { uri: string; name: string; mimeType: PhotoMimeType; size: number };

export type DetailField = "surname" | "givenNames" | "documentNumber" | "dateOfBirth" | "expiresOn" | "tin";

export type IdentityErrorCode =
  /** No check yet, so there is nothing to add a photo or details to. */
  | "not_found"
  /** The check is with us, or decided, so it cannot be changed now. */
  | "cannot_change"
  /** That photo is not one this document has, or not one staff asked for again. */
  | "photo_not_needed"
  /** Not a JPEG, PNG or WebP, larger than 10 MB, or not really a picture. */
  | "photo_not_accepted"
  /** The details were refused. `fields` says which and why. */
  | "invalid_details"
  /** Something is still missing. `missing` says what. */
  | "incomplete"
  | "offline"
  | "unavailable";

export class IdentityError extends Error {
  readonly code: IdentityErrorCode;
  /** For `invalid_details`: each refused field and the API's reason code. */
  readonly fields: { field: DetailField; code: string }[];
  /** For `incomplete`: photo kinds and "details". */
  readonly missing: string[];

  constructor(
    code: IdentityErrorCode,
    { fields = [], missing = [] }: { fields?: { field: DetailField; code: string }[]; missing?: string[] } = {},
  ) {
    super(code);
    this.name = "IdentityError";
    this.code = code;
    this.fields = fields;
    this.missing = missing;
  }
}

export type IdentityClient = {
  /** The client's check, or null before they have chosen a document. */
  getCheck(): Promise<IdentityCheck | null>;
  /** Start the check, or change the document while it is a draft. Details must then be confirmed again. */
  chooseDocument(documentType: DocumentType, country: string): Promise<IdentityCheck>;
  /** Sends one photo. It replaces the one of the same kind. */
  sendPhoto(kind: PhotoKind, file: PhotoFile): Promise<IdentityCheck>;
  confirmDetails(details: IdentityDetails): Promise<IdentityCheck>;
  /** Hands the check in, or the photos staff asked for. */
  submit(): Promise<IdentityCheck>;
};

const errorCopy: Record<IdentityErrorCode, string> = {
  not_found: "Choose your ID document first.",
  cannot_change: "Your ID check is with us now, so it cannot be changed. Pull down to see where it stands.",
  photo_not_needed: "We do not need that photo now.",
  photo_not_accepted: "We could not use that photo. Take a JPG, PNG or WebP photo under 10 MB.",
  invalid_details: "Some details are not right. Check the fields marked in red.",
  incomplete: "Something is still missing. Check each step and try again.",
  offline: "You seem to be offline. Check your connection and try again.",
  unavailable: "ID checks are not available right now. Try again in a moment.",
};

export function describeIdentityError(error: unknown): string {
  return error instanceof IdentityError ? errorCopy[error.code] : errorCopy.unavailable;
}

export const documentCopy: Record<DocumentType, { name: string; sub: string; numberLabel: string; front: string }> = {
  national_id: {
    name: "National ID",
    sub: "Fastest. Front and back.",
    numberLabel: "National ID number (NIN)",
    front: "Front of your National ID",
  },
  passport: {
    name: "Passport",
    sub: "Photo page only",
    numberLabel: "Passport number",
    front: "Photo page of your passport",
  },
  driving_permit: {
    name: "Driving permit",
    sub: "Front and back",
    numberLabel: "Driving permit number",
    front: "Front of your driving permit",
  },
  refugee_id: {
    name: "Refugee ID",
    sub: "Front and back, checked by a person",
    numberLabel: "Refugee ID number",
    front: "Front of your Refugee ID",
  },
};

export const DOCUMENT_TYPES = Object.keys(documentCopy) as DocumentType[];

/** The photos each document needs. A passport has no back. Mirrors the backend's required_photos. */
export function requiredPhotos(documentType: DocumentType): PhotoKind[] {
  return documentType === "passport" ? ["front", "selfie"] : ["front", "back", "selfie"];
}

export function photoTitle(kind: PhotoKind, documentType: DocumentType): string {
  if (kind === "selfie") return "Take a selfie";
  if (kind === "front") return documentCopy[documentType].front;
  return `Back of your ${documentType === "national_id" || documentType === "refugee_id" ? documentCopy[documentType].name : documentCopy[documentType].name.toLowerCase()}`;
}

/** How a photo is named in a list, such as "the back of your ID". */
export function photoName(kind: PhotoKind, documentType: DocumentType): string {
  if (kind === "selfie") return "Your selfie";
  if (documentType === "passport") return "The photo page";
  return kind === "front" ? "The front of your ID" : "The back of your ID";
}

/** Being checked, as the client sees it. Which of the three it is stays with us. */
export function beingChecked(status: CheckStatus): boolean {
  return status === "submitted" || status === "needs_review" || status === "with_compliance";
}

/** "1001004827" as "1001 004 827", the way the canvas shows it. */
export function formatAccountNumber(number: string): string {
  const digits = number.replace(/\D/g, "");
  if (digits.length !== 10) return number;
  return `${digits.slice(0, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
}
