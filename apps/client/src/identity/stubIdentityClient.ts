import { kampalaToday } from "./photo";
import {
  DetailField,
  DocumentType,
  IdentityCheck,
  IdentityClient,
  IdentityDetails,
  IdentityError,
  LivenessAttempt,
  PhotoKind,
  requiredPhotos,
} from "./types";

/**
 * An in-memory stand-in for `/client/identity-check`, so the ID check screens
 * can be tried in a demo build and in tests. Nothing is checked, nothing is
 * stored, and everything is forgotten when the app reloads. `resolveIdentity.ts`
 * keeps it out of production builds.
 *
 * It refuses what the API refuses: photos a document does not have, changes
 * once the check is handed in, details that are not valid, and handing in a
 * check with something missing.
 *
 * There is no staff in the demo, so the last digit of the document number
 * stands in for their decision. It is made the second time the check is read
 * after it was handed in, so the status screen first shows it being checked:
 * - ending in 1: staff ask for a new selfie, with a message
 * - ending in 2: rejected
 * - ending in 3: approved, with an account number
 * - anything else: stays with staff
 *
 * The demo account with a passkey (+256 772 123 456) starts approved.
 */

type StubOptions = {
  delayMs?: number;
  seeded?: boolean;
  now?: () => number;
  /** Offer a live check for the selfie, as the API does when a liveness provider is set. */
  liveness?: boolean;
};

/** The demo's live check. It moves on by itself as it is read, as the worker would. */
type StoredAttempt = LivenessAttempt & { readsLeft: number };

/** Live checks per ID check, as in the backend's MAX_ATTEMPTS. */
export const DEMO_LIVENESS_ATTEMPTS = 10;
/** Reads of an open live check before the demo answers it. */
const READS_UNTIL_ANSWER = 2;
/** A placeholder page. The demo opens nothing real. */
export const DEMO_LIVENESS_PAGE = "https://example.org/finvest-live-check-demo";

type Stored = Omit<IdentityCheck, "missing"> & {
  /** Photos sent since staff asked for some again. */
  retaken: Set<PhotoKind>;
  /** Handed in, and staff's demo decision not made yet. */
  awaitingDecision: boolean;
  /** Reads of the check still to answer before the decision is made. */
  readsBeforeDecision: number;
};

export const DEMO_RETAKE_MESSAGE =
  "Your selfie is too dark to match to your ID. Take it again facing a window, without a hat or glasses.";
export const DEMO_REJECT_MESSAGE =
  "The ID you sent belongs to someone who already has a Finvest account. Write to us in Help if this is a mistake.";

const COUNTRY = /^[A-Z]{2}$/;
const NAME = /^[\p{L}]+(?:[ '\-.][\p{L}]+)*\.?$/u;
const DOCUMENT_NUMBER = /^[A-Z0-9]{5,32}$/;
const TIN = /^[0-9]{10}$/;

function seededCheck(): Stored {
  return {
    id: "demo-check-approved",
    status: "approved",
    documentType: "national_id",
    documentCountry: "UG",
    details: {
      surname: "NAKATO",
      givenNames: "Amara",
      documentNumber: "CF94012345A2KX",
      dateOfBirth: "1994-06-22",
      expiresOn: "2030-09-18",
      tin: null,
    },
    detailsConfirmed: true,
    photos: ["front", "back", "selfie"],
    liveSelfie: true,
    retakePhotos: [],
    clientMessage: null,
    submittedAt: "2026-11-01T08:12:00Z",
    accountNumber: "1001004827",
    retaken: new Set(),
    awaitingDecision: false,
    readsBeforeDecision: 0,
  };
}

function missingFor(check: Stored): string[] {
  const missing: string[] = requiredPhotos(check.documentType).filter((kind) => !check.photos.includes(kind));
  if (!check.detailsConfirmed) missing.push("details");
  return missing;
}

function view(check: Stored): IdentityCheck {
  const { retaken: _retaken, awaitingDecision: _awaiting, readsBeforeDecision: _reads, ...rest } = check;
  return {
    ...rest,
    details: rest.details ? { ...rest.details } : null,
    photos: [...rest.photos],
    retakePhotos: [...rest.retakePhotos],
    missing: missingFor(check),
  };
}

function yearsBetween(earlier: string, later: string): number {
  const [ey, em, ed] = earlier.split("-").map(Number) as [number, number, number];
  const [ly, lm, ld] = later.split("-").map(Number) as [number, number, number];
  return ly - ey - (lm < em || (lm === em && ld < ed) ? 1 : 0);
}

function problems(details: IdentityDetails, today: string): { field: DetailField; code: string }[] {
  const found: { field: DetailField; code: string }[] = [];
  if (!NAME.test(details.surname)) found.push({ field: "surname", code: "invalid_name" });
  if (!NAME.test(details.givenNames)) found.push({ field: "givenNames", code: "invalid_name" });
  if (!DOCUMENT_NUMBER.test(details.documentNumber)) {
    found.push({ field: "documentNumber", code: "invalid_document_number" });
  }
  if (details.dateOfBirth > today) found.push({ field: "dateOfBirth", code: "in_the_future" });
  else if (yearsBetween(details.dateOfBirth, today) < 18) found.push({ field: "dateOfBirth", code: "under_age" });
  if (details.expiresOn < today) found.push({ field: "expiresOn", code: "document_expired" });
  if (details.tin !== null && !TIN.test(details.tin)) found.push({ field: "tin", code: "invalid_tin" });
  return found;
}

let nextId = 1;

export function createStubIdentityClient({
  delayMs = 400,
  seeded = false,
  now = Date.now,
  liveness = false,
}: StubOptions = {}): IdentityClient {
  let check: Stored | null = seeded ? seededCheck() : null;
  const attempts: StoredAttempt[] = [];

  function selfieEditable(held: Stored): boolean {
    return held.status === "draft" || (held.status === "awaiting_client" && held.retakePhotos.includes("selfie"));
  }

  /** The demo's stand-in for the provider: a number ending in 4 is not a live person. */
  function advance(attempt: StoredAttempt): void {
    if (attempt.status === "starting") {
      attempt.status = "open";
      attempt.url = DEMO_LIVENESS_PAGE;
      return;
    }
    if (attempt.status !== "open") return;
    if (attempt.readsLeft > 0) {
      attempt.readsLeft -= 1;
      return;
    }
    attempt.url = null;
    if (check?.details?.documentNumber.endsWith("4")) {
      attempt.status = "failed";
      attempt.failureCode = "not_live";
      return;
    }
    attempt.status = "passed";
    if (check && selfieEditable(check)) {
      if (!check.photos.includes("selfie")) check.photos.push("selfie");
      check.liveSelfie = true;
      if (check.status === "awaiting_client") check.retaken.add("selfie");
      attempt.photoUsed = true;
    } else {
      attempt.failureCode = "check_moved_on";
    }
  }

  function attemptView(attempt: StoredAttempt): LivenessAttempt {
    const { readsLeft: _reads, ...rest } = attempt;
    return { ...rest };
  }

  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  function editable(): Stored {
    if (!check) throw new IdentityError("not_found");
    if (check.status !== "draft" && check.status !== "awaiting_client") throw new IdentityError("cannot_change");
    return check;
  }

  /** Staff's stand-in decision, from the document number's last digit. */
  function decide(held: Stored): void {
    if (!held.awaitingDecision) return;
    if (held.readsBeforeDecision > 0) {
      held.readsBeforeDecision -= 1;
      return;
    }
    held.awaitingDecision = false;
    const last = held.details?.documentNumber.slice(-1);
    if (last === "1" && held.retakePhotos.length === 0 && held.clientMessage === null) {
      held.status = "awaiting_client";
      held.retakePhotos = ["selfie"];
      held.clientMessage = DEMO_RETAKE_MESSAGE;
      held.retaken = new Set();
    } else if (last === "2") {
      held.status = "rejected";
      held.clientMessage = DEMO_REJECT_MESSAGE;
    } else if (last === "3" || last === "1") {
      held.status = "approved";
      held.clientMessage = null;
      held.accountNumber = `100100${String(4000 + nextId++).padStart(4, "0")}`;
    }
  }

  return {
    async getCheck() {
      await pause();
      if (check) decide(check);
      return check ? view(check) : null;
    },

    async chooseDocument(documentType: DocumentType, country: string) {
      await pause();
      if (!COUNTRY.test(country)) throw new IdentityError("invalid_details");
      if (!check) {
        check = {
          id: `demo-check-${nextId++}`,
          status: "draft",
          documentType,
          documentCountry: country,
          details: null,
          detailsConfirmed: false,
          photos: [],
          liveSelfie: false,
          retakePhotos: [],
          clientMessage: null,
          submittedAt: null,
          accountNumber: null,
          retaken: new Set(),
          awaitingDecision: false,
          readsBeforeDecision: 0,
        };
      } else if (check.documentType !== documentType || check.documentCountry !== country) {
        if (check.status !== "draft") throw new IdentityError("cannot_change");
        check.documentType = documentType;
        check.documentCountry = country;
        // Details were read from the other document, so they must be confirmed again.
        check.detailsConfirmed = false;
        check.photos = check.photos.filter((kind) => requiredPhotos(documentType).includes(kind));
      }
      return view(check);
    },

    async sendPhoto(kind, file) {
      await pause();
      const held = editable();
      if (!requiredPhotos(held.documentType).includes(kind)) throw new IdentityError("photo_not_needed");
      if (held.status === "awaiting_client" && !held.retakePhotos.includes(kind)) {
        throw new IdentityError("photo_not_needed");
      }
      if (file.size <= 0 || file.size > 10 * 1024 * 1024) throw new IdentityError("photo_not_accepted");
      if (!held.photos.includes(kind)) held.photos.push(kind);
      if (kind === "selfie") held.liveSelfie = false;
      if (held.status === "awaiting_client") held.retaken.add(kind);
      return view(held);
    },

    async confirmDetails(details) {
      await pause();
      const held = editable();
      if (held.status !== "draft") throw new IdentityError("cannot_change");
      const found = problems(details, kampalaToday(now()));
      if (found.length > 0) throw new IdentityError("invalid_details", { fields: found });
      held.details = { ...details };
      held.detailsConfirmed = true;
      return view(held);
    },

    async submit() {
      await pause();
      const held = editable();
      const missing = missingFor(held);
      if (held.status === "awaiting_client") {
        missing.push(...held.retakePhotos.filter((kind) => !held.retaken.has(kind)));
      }
      if (missing.length > 0) throw new IdentityError("incomplete", { missing });
      held.status = "needs_review";
      held.submittedAt = new Date(now()).toISOString();
      held.retakePhotos = [];
      held.awaitingDecision = true;
      held.readsBeforeDecision = 1;
      return view(held);
    },

    async getLiveness() {
      await pause();
      const latest = attempts[attempts.length - 1] ?? null;
      if (latest) advance(latest);
      let problem: string | null = null;
      if (!liveness) problem = null;
      else if (!check) problem = "no_check";
      else if (!selfieEditable(check)) problem = "selfie_not_editable";
      else if (attempts.length >= DEMO_LIVENESS_ATTEMPTS) problem = "too_many_attempts";
      return {
        available: liveness,
        problem,
        attemptsLeft: Math.max(0, DEMO_LIVENESS_ATTEMPTS - attempts.length),
        latest: latest ? attemptView(latest) : null,
      };
    },

    async startLiveness() {
      await pause();
      if (!liveness) throw new IdentityError("unavailable");
      const held = editable();
      if (!selfieEditable(held)) throw new IdentityError("cannot_change");
      if (attempts.length >= DEMO_LIVENESS_ATTEMPTS) throw new IdentityError("liveness_used_up");
      for (const waiting of attempts) {
        if (waiting.status === "starting" || waiting.status === "open") {
          waiting.status = "expired";
          waiting.url = null;
        }
      }
      const attempt: StoredAttempt = {
        id: `demo-liveness-${nextId++}`,
        status: "starting",
        url: null,
        failureCode: null,
        photoUsed: false,
        expiresAt: new Date(now() + 30 * 60 * 1000).toISOString(),
        readsLeft: READS_UNTIL_ANSWER,
      };
      attempts.push(attempt);
      return attemptView(attempt);
    },
  };
}
