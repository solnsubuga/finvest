import type { ClaimStateView } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";

import { Claim, ClaimClient, ClaimError } from "./types";

/**
 * The claim screens' contract, answered by the real API: `/client/claim` in
 * backend/app/modules/migration.
 */

type Action = "read" | "hand_in" | "problem" | "other";

export function toClaimError(error: unknown, action: Action): ClaimError {
  if (error instanceof ClaimError) return error;
  if (!(error instanceof ApiError)) return new ClaimError("unavailable");
  if (error.code === "offline" || error.code === "timeout") return new ClaimError("offline");
  switch (error.code) {
    case "not_found":
      return new ClaimError("not_found");
    case "conflict":
      return new ClaimError("cannot_change");
    case "validation_failed":
      if (action === "hand_in") return new ClaimError("incomplete", error.fields.map((problem) => problem.field));
      if (action === "problem") return new ClaimError("not_accepted");
      return new ClaimError("unavailable");
    default:
      return new ClaimError("unavailable");
  }
}

export function toClaim(view: ClaimStateView): Claim {
  return { status: view.status, identityStep: view.identity_step };
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
        // A client who signed up in the app has no claim. That is not a failure.
        if (error instanceof ApiError && error.code === "not_found") return null;
        throw toClaimError(error, "read");
      }
    },

    handIn: () => run("hand_in", async () => toClaim(await api("POST /client/claim/identity", {}))),

    confirm: () => run("other", async () => toClaim(await api("POST /client/claim/confirm", {}))),

    reportProblem: (description) =>
      run("problem", async () =>
        toClaim(await api("POST /client/claim/problem", { body: { description: description.trim() } })),
      ),
  };
}
