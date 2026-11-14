import type { ClientClosureView, NextOfKinView, RiskProfileView } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";

import { saveMyData } from "./saveData";
import { Closure, NextOfKin, Person, ProfileClient, ProfileError, refusalFor, RiskAnswers, RiskProfile } from "./types";

/** The profile screens' contract, answered by the client routes in docs/client-profile.md. */

export function toNextOfKin(view: NextOfKinView): NextOfKin {
  return {
    people: view.people.map((p) => ({
      fullName: p.full_name,
      relationship: p.relationship,
      phone: p.phone,
      idNumber: p.id_number,
    })),
    updatedAt: view.updated_at,
  };
}

export function toRiskProfile(view: RiskProfileView): RiskProfile {
  return {
    questions: view.questions.map((q) => ({ code: q.code, options: q.options.map((o) => o.code) })),
    latest: view.latest
      ? {
          answers: view.latest.answers as RiskAnswers,
          profile: view.latest.profile,
          longestTermDays: view.latest.longest_term_days,
          answeredAt: view.latest.answered_at,
        }
      : null,
    due: view.due,
  };
}

export function toClosure(view: ClientClosureView): Closure {
  const s = view.standing;
  return {
    standing: {
      runningInvestments: s.running_investments,
      owed: s.owed.map((m) => ({ minor: m.amount_minor, currency: m.currency as "UGX" | "USD" })),
      openDeposits: s.open_deposits,
      openPayouts: s.open_payouts,
      openGoals: s.open_goals,
      livePlans: s.live_plans,
      blockers: s.blockers,
    },
    request: view.request
      ? {
          id: view.request.id,
          status: view.request.status,
          reason: view.request.reason,
          requestedAt: view.request.requested_at,
          declineNote: view.request.decision_note,
        }
      : null,
  };
}

/** Turns an API refusal into what the screens understand. Nothing else is shown. */
function translate(error: unknown): ProfileError {
  if (error instanceof ProfileError) return error;
  if (error instanceof ApiError) {
    if (error.code === "validation_failed") {
      return new ProfileError(
        "invalid",
        error.fields.map((f) => ({ field: f.field.replace(/^body\./, ""), code: f.code })),
      );
    }
    if (error.code === "conflict") {
      return error.fields.some((f) => f.field === "blockers")
        ? new ProfileError("blocked", error.fields)
        : new ProfileError("conflict");
    }
    // Offline and timed out never reached an answer; anything else was a refusal.
    if (error.code === "offline" || error.code === "timeout") return new ProfileError("unavailable");
    return refusalFor(error.code, error.status);
  }
  return new ProfileError("unavailable");
}

async function wrap<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw translate(error);
  }
}

function body(person: Person) {
  return {
    full_name: person.fullName,
    relationship: person.relationship,
    phone: person.phone,
    id_number: person.idNumber,
  };
}

export function createApiProfileClient(): ProfileClient {
  return {
    getNextOfKin: () => wrap(async () => toNextOfKin(await api("GET /client/next-of-kin", {}))),
    saveNextOfKin: (people) =>
      wrap(async () =>
        toNextOfKin(await api("PUT /client/next-of-kin", { body: { people: people.map(body) } })),
      ),
    getRiskProfile: () => wrap(async () => toRiskProfile(await api("GET /client/risk-profile", {}))),
    answerRiskQuestions: (answers) =>
      wrap(async () => toRiskProfile(await api("PUT /client/risk-profile", { body: { answers } }))),
    getClosure: () => wrap(async () => toClosure(await api("GET /client/account-closure", {}))),
    askToClose: (reason) =>
      wrap(async () => toClosure(await api("POST /client/account-closure", { body: { reason } }))),
    cancelClosure: (requestId) =>
      wrap(async () =>
        toClosure(
          await api("POST /client/account-closure/{request_id}/cancellation", {
            params: { request_id: requestId },
          }),
        ),
      ),
    downloadMyData: () =>
      wrap(async () => {
        const day = new Date().toISOString().slice(0, 10);
        await saveMyData(`finvest-my-data-${day}.json`);
      }),
    freezeAccount: (reason) =>
      wrap(async () => {
        const view = await api("POST /client/account-freeze", { body: { reason } });
        return { frozenAt: view.frozen_at };
      }),
  };
}
