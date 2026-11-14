import { IdentityError } from "@/identity/types";

import { DEMO_CLAIM_FULL_CHECK, DEMO_CLAIM_SELFIE } from "./demo";
import {
  Claim,
  ClaimCheck,
  ClaimClient,
  ClaimError,
  ClaimPhotoKind,
  CLAIM_PHOTOS,
  IdentityStep,
  MAX_PROBLEM_LENGTH,
} from "./types";

/**
 * An in-memory stand-in for `/client/claim` and `/client/claim-check`, so the
 * claim screens can be tried in a demo build and in tests. Nothing is checked
 * or stored, and everything is forgotten when the app reloads. The provider
 * keeps it out of production builds, as it does the other demo clients.
 *
 * It refuses what the API refuses: a step out of turn, handing in with a
 * photo missing, and an empty or too long problem.
 *
 * There is no staff in the demo, so the decision is made the second time the
 * claim is read after handing in, and the screen first shows it being checked.
 * The first selfie claim check handed in is asked for a new selfie, with a
 * message, so the retake can be tried. Photos sent again are matched, and a
 * full ID check is confirmed.
 */

type StubOptions = {
  /** The signed-in number. Only the demo's two moved accounts have a claim. */
  phone: string | null;
  delayMs?: number;
  /** Hands the demo ID check in, for a client who takes the full check. */
  submitIdCheck?: () => Promise<unknown>;
};

const READS_UNTIL_DECISION = 2;

export const DEMO_CLAIM_RETAKE_MESSAGE =
  "Your selfie is too dark to compare with your ID. Take it again facing a window, without a hat or glasses.";

export function stepFor(phone: string | null): IdentityStep | null {
  if (phone === DEMO_CLAIM_SELFIE.phone) return "selfie";
  if (phone === DEMO_CLAIM_FULL_CHECK.phone) return "full_id_check";
  return null;
}

export function createStubClaimClient(options: StubOptions): ClaimClient {
  const delayMs = options.delayMs ?? 450;
  const step = stepFor(options.phone);
  const claim: Claim | null = step ? { status: "signed_in", identityStep: step } : null;
  let check: ClaimCheck | null = null;
  let readsBeforeDecision = 0;
  let askedForRetake = false;

  const wait = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  const mine = (): Claim => {
    if (!claim) throw new ClaimError("not_found");
    return claim;
  };

  const editable = (): ClaimCheck => {
    if (!check) throw new ClaimError("not_found");
    if (check.status !== "draft" && check.status !== "awaiting_client") throw new ClaimError("cannot_change");
    return check;
  };

  const missing = (photos: ClaimPhotoKind[]) => CLAIM_PHOTOS.filter((kind) => !photos.includes(kind));

  /** The demo's stand-in for staff: decides once the claim has been read enough times. */
  const decideIfDue = () => {
    if (!claim || claim.status !== "identity_submitted" || readsBeforeDecision <= 0) return;
    readsBeforeDecision -= 1;
    if (readsBeforeDecision > 0) return;
    if (check && !askedForRetake) {
      askedForRetake = true;
      check = { ...check, status: "awaiting_client", retakePhotos: ["selfie"], retaken: [], clientMessage: DEMO_CLAIM_RETAKE_MESSAGE };
      return;
    }
    claim.status = "identity_confirmed";
    if (check) check = { ...check, status: "matched" };
  };

  return {
    async getClaim() {
      await wait();
      decideIfDue();
      return claim ? { ...claim } : null;
    },

    async getCheck() {
      await wait();
      return check ? { ...check } : null;
    },

    async startCheck() {
      await wait();
      if (mine().identityStep !== "selfie") throw new ClaimError("not_found");
      check ??= {
        id: "demo-claim-check",
        status: "draft",
        photos: [],
        missing: [...CLAIM_PHOTOS],
        retakePhotos: [],
        retaken: [],
        clientMessage: null,
      };
      return { ...check };
    },

    async sendPhoto(kind, file) {
      await wait();
      const current = editable();
      if (file.size > 10 * 1024 * 1024) throw new ClaimError("photo_not_accepted");
      if (current.status === "awaiting_client" && !current.retakePhotos.includes(kind)) {
        throw new ClaimError("cannot_change");
      }
      const photos = current.photos.includes(kind) ? current.photos : [...current.photos, kind];
      const retaken =
        current.status === "awaiting_client" && !current.retaken.includes(kind)
          ? [...current.retaken, kind]
          : current.retaken;
      check = { ...current, photos, missing: missing(photos), retaken };
      return { ...check };
    },

    async handIn() {
      await wait();
      const current = mine();
      if (current.status !== "signed_in") throw new ClaimError("cannot_change");
      if (current.identityStep === "selfie") {
        const photos = editable();
        if (photos.missing.length > 0) throw new ClaimError("incomplete", [...photos.missing]);
        check = { ...photos, status: "needs_review" };
      } else {
        try {
          await options.submitIdCheck?.();
        } catch (caught) {
          if (caught instanceof IdentityError && caught.code === "incomplete") {
            throw new ClaimError("incomplete", caught.missing);
          }
          throw caught instanceof IdentityError ? new ClaimError("unavailable") : caught;
        }
      }
      current.status = "identity_submitted";
      readsBeforeDecision = READS_UNTIL_DECISION;
      return { ...current };
    },

    async sendRetakes() {
      await wait();
      const current = editable();
      if (current.status !== "awaiting_client") throw new ClaimError("cannot_change");
      const left = current.retakePhotos.filter((kind) => !current.retaken.includes(kind));
      if (left.length > 0) throw new ClaimError("incomplete", left);
      check = { ...current, status: "needs_review", retakePhotos: [], retaken: [], clientMessage: null };
      readsBeforeDecision = READS_UNTIL_DECISION;
      return { ...check };
    },

    async confirm() {
      await wait();
      const current = mine();
      if (current.status !== "identity_confirmed") throw new ClaimError("cannot_change");
      current.status = "confirmed";
      return { ...current };
    },

    async reportProblem(description) {
      await wait();
      const current = mine();
      if (current.status !== "identity_confirmed") throw new ClaimError("cannot_change");
      const text = description.trim();
      if (!text || text.length > MAX_PROBLEM_LENGTH) throw new ClaimError("not_accepted");
      current.status = "problem_reported";
      return { ...current };
    },
  };
}
