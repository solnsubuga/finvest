import { KycError, type CheckDetail, type Decision, type KycApi, type PhotoKind, type QueueItem } from "./api.ts";
import { maskDocumentNumber } from "./labels.ts";

/**
 * Sample identity checks for `pnpm dev` and previews. Not real people and not
 * connected to Finvest. Decisions change only this browser tab's copy.
 */

const REVIEW: readonly Decision[] = ["approve", "ask_for_photos", "reject", "escalate"];
const COMPLIANCE: readonly Decision[] = ["approve", "ask_for_photos", "reject"];
const ORDER = ["needs_review", "with_compliance", "awaiting_client", "submitted"];

function ago(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const photos = (...kinds: PhotoKind[]) => kinds.map((kind) => ({ kind, url: null, live: false }));
/** The same, with the selfie captured by a live check. */
const withLiveSelfie = (...kinds: PhotoKind[]) => photos(...kinds).map((photo) => ({ ...photo, live: photo.kind === "selfie" }));

/** The whole document numbers, which the routes give out only one at a time. */
const NUMBERS: Record<string, string> = {};

function sample(check: Omit<CheckDetail, "documentNumberMasked"> & { documentNumber: string }): CheckDetail {
  const { documentNumber, ...rest } = check;
  NUMBERS[check.id] = documentNumber;
  return { ...rest, documentNumberMasked: maskDocumentNumber(documentNumber) };
}

function sampleChecks(): CheckDetail[] {
  return [
    sample({
      id: "-OdSample0000000001A",
      name: "David Mugisha",
      documentType: "national_id",
      status: "needs_review",
      submittedAt: ago(120),
      reviewFlags: ["selfie_match_unsure"],
      documentCountry: "UG",
      surname: "MUGISHA",
      givenNames: "David",
      documentNumber: "CM90123456784PL",
      dateOfBirth: "1990-03-14",
      documentExpiresOn: "2031-08-02",
      photos: withLiveSelfie("front", "back", "selfie"),
      automaticChecks: [
        { check: "document_genuine", verdict: "pass", score: null },
        { check: "selfie_match", verdict: "unsure", score: 62 },
        { check: "screening", verdict: "pass", score: null },
        { check: "liveness", verdict: "pass", score: null },
      ],
      screening: "clear",
      retakePhotos: [],
      clientMessage: null,
      accountNumber: null,
      allowed: REVIEW,
    }),
    sample({
      id: "-OdSample0000000002A",
      name: "Ruth Akello",
      documentType: "passport",
      status: "needs_review",
      submittedAt: ago(5),
      reviewFlags: ["no_automatic_checks"],
      documentCountry: "UG",
      surname: "AKELLO",
      givenNames: "Ruth",
      documentNumber: "A00123456",
      dateOfBirth: "1988-11-02",
      documentExpiresOn: "2029-01-19",
      photos: photos("front", "selfie"),
      automaticChecks: [],
      screening: null,
      retakePhotos: [],
      clientMessage: null,
      accountNumber: null,
      allowed: REVIEW,
    }),
    sample({
      id: "-OdSample0000000003A",
      name: "Grace Namara",
      documentType: "national_id",
      status: "with_compliance",
      submittedAt: ago(60 * 26),
      reviewFlags: ["screening_unsure"],
      documentCountry: "UG",
      surname: "NAMARA",
      givenNames: "Grace",
      documentNumber: "CF85987654321QR",
      dateOfBirth: "1985-07-30",
      documentExpiresOn: "2028-05-11",
      photos: withLiveSelfie("front", "back", "selfie"),
      automaticChecks: [
        { check: "document_genuine", verdict: "pass", score: null },
        { check: "selfie_match", verdict: "pass", score: 96 },
        { check: "screening", verdict: "unsure", score: null },
        { check: "liveness", verdict: "pass", score: null },
      ],
      screening: "possible_match",
      retakePhotos: [],
      clientMessage: null,
      accountNumber: null,
      allowed: COMPLIANCE,
    }),
    sample({
      id: "-OdSample0000000004A",
      name: "Joseph Nakato",
      documentType: "driving_permit",
      status: "awaiting_client",
      submittedAt: ago(60 * 5),
      reviewFlags: [],
      documentCountry: "UG",
      surname: "NAKATO",
      givenNames: "Joseph",
      documentNumber: "DP1029384756",
      dateOfBirth: "1992-01-09",
      documentExpiresOn: "2027-12-01",
      photos: photos("front", "back", "selfie"),
      automaticChecks: [],
      screening: "pending",
      retakePhotos: ["front"],
      clientMessage: "The front of your permit is blurred. Please take it again in good light.",
      accountNumber: null,
      allowed: [],
    }),
  ];
}

/** Luhn check digit, so sample account numbers have the real format. */
function sampleAccountNumber(seed: number): string {
  const payload = `10${String(4827 + seed).padStart(7, "0")}`;
  let total = 0;
  [...payload].reverse().forEach((character, index) => {
    let digit = Number(character);
    if (index % 2 === 0) digit = digit * 2 > 9 ? digit * 2 - 9 : digit * 2;
    total += digit;
  });
  return payload + String((10 - (total % 10)) % 10);
}

export function createStubKycApi(delayMs = 150): KycApi {
  const checks = new Map(sampleChecks().map((check) => [check.id, check]));
  let issued = 0;
  const wait = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new KycError("unavailable"));
        }
      }, delayMs),
    );

  function decide(id: string, decision: Decision, change: (check: CheckDetail) => Partial<CheckDetail>): CheckDetail {
    const check = checks.get(id);
    if (!check) throw new KycError("not_found");
    if (!check.allowed.includes(decision)) throw new KycError("conflict");
    const next = { ...check, ...change(check) };
    checks.set(id, next);
    return next;
  }

  return {
    listQueue: () =>
      wait(() =>
        [...checks.values()]
          .filter((check) => ORDER.includes(check.status))
          .sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || a.submittedAt.localeCompare(b.submittedAt))
          .map(({ id, name, documentType, status, submittedAt, reviewFlags }): QueueItem => ({
            id,
            name,
            documentType,
            status,
            submittedAt,
            reviewFlags,
          })),
      ),
    getCheck: (id) =>
      wait(() => {
        const check = checks.get(id);
        if (!check) throw new KycError("not_found");
        return check;
      }),
    showDocumentNumber: (id) =>
      wait(() => {
        const number = NUMBERS[id];
        if (!number) throw new KycError("not_found");
        return number;
      }),
    approve: (id) =>
      wait(() => decide(id, "approve", () => ({ status: "approved", allowed: [], accountNumber: sampleAccountNumber(issued++) }))),
    reject: (id, _reason, clientMessage) =>
      wait(() => decide(id, "reject", () => ({ status: "rejected", allowed: [], clientMessage: clientMessage || null }))),
    askForPhotos: (id, photos, clientMessage) =>
      wait(() => decide(id, "ask_for_photos", () => ({ status: "awaiting_client", allowed: [], retakePhotos: photos, clientMessage }))),
    escalate: (id) => wait(() => decide(id, "escalate", () => ({ status: "with_compliance", allowed: COMPLIANCE }))),
  };
}
