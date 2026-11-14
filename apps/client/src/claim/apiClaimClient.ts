import type { ClaimStateView, ClientClaimCheck } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";
import { uploadable } from "@/identity/apiIdentityClient";

import { Claim, ClaimCheck, ClaimClient, ClaimError, ClaimPhotoKind, CLAIM_PHOTOS } from "./types";

/**
 * The claim screens' contract, answered by the real API: `/client/claim` in
 * backend/app/modules/migration, and `/client/claim-check` in backend/app/modules/kyc.
 */

type Action = "read" | "photo" | "hand_in" | "problem" | "other";

export function toClaimError(error: unknown, action: Action): ClaimError {
  if (error instanceof ClaimError) return error;
  if (!(error instanceof ApiError)) return new ClaimError("unavailable");
  if (error.code === "offline" || error.code === "timeout") return new ClaimError("offline");
  switch (error.code) {
    case "not_found":
      return new ClaimError("not_found");
    case "conflict":
      return new ClaimError("cannot_change");
    case "payload_too_large":
      return new ClaimError("photo_not_accepted");
    case "validation_failed":
      if (action === "photo") return new ClaimError("photo_not_accepted");
      if (action === "hand_in") return new ClaimError("incomplete", error.fields.map((problem) => problem.field));
      if (action === "problem") return new ClaimError("not_accepted");
      return new ClaimError("unavailable");
    default:
      return new ClaimError("unavailable");
  }
}

function isClaimPhoto(kind: string): kind is ClaimPhotoKind {
  return (CLAIM_PHOTOS as readonly string[]).includes(kind);
}

export function toClaim(view: ClaimStateView): Claim {
  return { status: view.status, identityStep: view.identity_step };
}

export function toClaimCheck(view: ClientClaimCheck): ClaimCheck {
  return {
    id: view.id,
    status: view.status,
    photos: view.photos.map((photo) => photo.kind).filter(isClaimPhoto),
    missing: view.missing.filter(isClaimPhoto),
    retakePhotos: view.retake_photos.filter(isClaimPhoto),
    retaken: view.retaken.filter(isClaimPhoto),
    clientMessage: view.client_message,
  };
}

async function run<T>(action: Action, work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toClaimError(error, action);
  }
}

export function createApiClaimClient(): ClaimClient {
  return {
    async getClaim() {
      try {
        return toClaim(await api("GET /client/claim", {}));
      } catch (error) {
        // A client who signed up in Finvest 2.0 has no claim. That is not a failure.
        if (error instanceof ApiError && error.code === "not_found") return null;
        throw toClaimError(error, "read");
      }
    },

    getCheck: () =>
      run("read", async () => {
        const { check } = await api("GET /client/claim-check", {});
        return check ? toClaimCheck(check) : null;
      }),

    startCheck: () => run("other", async () => toClaimCheck(await api("POST /client/claim-check", {}))),

    sendPhoto: (kind, file) =>
      run("photo", async () =>
        toClaimCheck(
          await api("PUT /client/claim-check/photos/{kind}", {
            params: { kind },
            body: { photo: await uploadable(file) },
            // A 10 MB photo on a slow mobile network takes longer than a JSON call.
            timeoutMs: 90_000,
          }),
        ),
      ),

    handIn: () => run("hand_in", async () => toClaim(await api("POST /client/claim/identity", {}))),

    sendRetakes: () =>
      run("hand_in", async () => toClaimCheck(await api("POST /client/claim-check/submit", {}))),

    confirm: () => run("other", async () => toClaim(await api("POST /client/claim/confirm", {}))),

    reportProblem: (description) =>
      run("problem", async () =>
        toClaim(await api("POST /client/claim/problem", { body: { description: description.trim() } })),
      ),
  };
}
