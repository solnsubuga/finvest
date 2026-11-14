import { IdentityError } from "@/identity/types";

import { DEMO_CLAIM } from "./demo";
import { Claim, ClaimClient, ClaimError, MAX_PROBLEM_LENGTH } from "./types";

/**
 * An in-memory stand-in for `/client/claim`, so the claim screens can be tried
 * in a demo build and in tests. Nothing is checked or stored, and everything is
 * forgotten when the app reloads. The provider keeps it out of production
 * builds, as it does the other demo clients.
 *
 * It refuses what the API refuses: a step out of turn, handing in an ID check
 * with something missing, and an empty or too long problem.
 *
 * There is no staff in the demo, so the ID check is approved the second time
 * the claim is read after handing in, and the screen first shows it being
 * checked.
 */

type StubOptions = {
  /** The signed-in number. Only the demo's moved account has a claim. */
  phone: string | null;
  delayMs?: number;
  /** Hands the demo ID check in. */
  submitIdCheck?: () => Promise<unknown>;
};

const READS_UNTIL_DECISION = 2;

export function createStubClaimClient(options: StubOptions): ClaimClient {
  const delayMs = options.delayMs ?? 450;
  const claim: Claim | null =
    options.phone === DEMO_CLAIM.phone ? { status: "signed_in", identityStep: "full_id_check" } : null;
  let readsBeforeDecision = 0;

  const wait = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  const mine = (): Claim => {
    if (!claim) throw new ClaimError("not_found");
    return claim;
  };

  /** The demo's stand-in for staff: approves once the claim has been read enough times. */
  const decideIfDue = () => {
    if (!claim || claim.status !== "identity_submitted" || readsBeforeDecision <= 0) return;
    readsBeforeDecision -= 1;
    if (readsBeforeDecision === 0) claim.status = "identity_confirmed";
  };

  return {
    async getClaim() {
      await wait();
      decideIfDue();
      return claim ? { ...claim } : null;
    },

    async handIn() {
      await wait();
      const current = mine();
      if (current.status !== "signed_in") throw new ClaimError("cannot_change");
      try {
        await options.submitIdCheck?.();
      } catch (caught) {
        if (caught instanceof IdentityError && caught.code === "incomplete") {
          throw new ClaimError("incomplete", caught.missing);
        }
        throw caught instanceof IdentityError ? new ClaimError("unavailable") : caught;
      }
      current.status = "identity_submitted";
      readsBeforeDecision = READS_UNTIL_DECISION;
      return { ...current };
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
