/**
 * A client's next of kin and their answers to the investing questions, as staff see them.
 *
 * Needs `clients.view`. ID numbers arrive masked, and the API records each look at the next
 * of kin in the audit trail.
 */

import { ApiError, type Http } from "@/api/http.ts";

export type Relationship = "spouse" | "child" | "parent" | "sibling" | "other_relative" | "friend" | "other";
export type InvestingProfile = "careful" | "balanced" | "long_term";

export interface Kin {
  fullName: string;
  relationship: Relationship;
  phone: string;
  idNumberMasked: string;
  /** Basis points: 10,000 is the whole. */
  shareBps: number;
}

export interface RiskAnswers {
  id: string;
  answers: Record<string, string>;
  profile: InvestingProfile;
  answeredAt: string;
  dueAgainAt: string;
}

export interface ClientProfile {
  /** Null when the API cannot open sealed details (the key is not configured). */
  nextOfKin: Kin[] | null;
  nextOfKinUpdatedAt: string | null;
  /** Newest first. */
  risk: RiskAnswers[];
}

export interface ProfileApi {
  profile(clientId: string): Promise<ClientProfile>;
}

export const RELATIONSHIP_LABELS: Record<Relationship, string> = {
  spouse: "Husband or wife",
  child: "Child",
  parent: "Parent",
  sibling: "Brother or sister",
  other_relative: "Other relative",
  friend: "Friend",
  other: "Someone else",
};

export const PROFILE_LABELS: Record<InvestingProfile, { label: string; detail: string }> = {
  careful: { label: "Careful", detail: "Terms of up to six months suit them." },
  balanced: { label: "Balanced", detail: "Terms of up to a year suit them." },
  long_term: { label: "Long term", detail: "Any term suits them." },
};

export const ANSWER_LABELS: Record<string, { question: string; options: Record<string, string> }> = {
  purpose: {
    question: "Investing for",
    options: { emergency_fund: "An emergency fund", planned_purchase: "Something planned", grow_savings: "Growing savings", retirement_or_children: "Retirement or children" },
  },
  need_back: {
    question: "Needs it back",
    options: { within_3_months: "Within 3 months", "3_to_12_months": "In 3 to 12 months", "1_to_3_years": "In 1 to 3 years", over_3_years: "In over 3 years" },
  },
  early_need: {
    question: "If needed early",
    options: { would_withdraw_early: "Would withdraw early", would_use_other_savings: "Would use other savings", would_wait: "Would wait" },
  },
  experience: {
    question: "Invested before",
    options: { none: "Never", savings_or_sacco: "Savings account or SACCO", bills_or_bonds: "Bills or bonds", shares_or_unit_trusts: "Shares or unit trusts" },
  },
};

/** "60%", or "33.34%". Integer arithmetic only. */
export function sharePercent(bps: number): string {
  const whole = Math.floor(bps / 100);
  const rest = bps % 100;
  return rest === 0 ? `${whole}%` : `${whole}.${String(rest).padStart(2, "0").replace(/0$/, "")}%`;
}

export class ProfileError extends Error {
  constructor(readonly code: "not_found" | "not_connected" | "unavailable") {
    super(code);
    this.name = "ProfileError";
  }
}

interface ProfileJson {
  next_of_kin: { full_name: string; relationship: Relationship; phone: string; id_number_masked: string; share_bps: number }[] | null;
  next_of_kin_updated_at: string | null;
  risk: { id: string; answers: Record<string, string>; profile: InvestingProfile; answered_at: string; due_again_at: string }[];
}

export function toProfile(json: ProfileJson): ClientProfile {
  return {
    nextOfKin:
      json.next_of_kin?.map((p) => ({ fullName: p.full_name, relationship: p.relationship, phone: p.phone, idNumberMasked: p.id_number_masked, shareBps: p.share_bps })) ?? null,
    nextOfKinUpdatedAt: json.next_of_kin_updated_at,
    risk: json.risk.map((r) => ({ id: r.id, answers: r.answers, profile: r.profile, answeredAt: r.answered_at, dueAgainAt: r.due_again_at })),
  };
}

export function createHttpProfileApi(http: Http): ProfileApi {
  return {
    async profile(clientId) {
      try {
        return toProfile(await http.request<ProfileJson>("GET", `/staff/clients/${encodeURIComponent(clientId)}/profile`));
      } catch (error) {
        if (error instanceof ApiError && error.code === "not_found") throw new ProfileError("not_found");
        throw new ProfileError("unavailable");
      }
    },
  };
}

export const notConnectedProfileApi: ProfileApi = { profile: () => Promise.reject(new ProfileError("not_connected")) };

/** Sample details for `pnpm dev`. Nothing here is real. */
export function createStubProfileApi(now: () => number = Date.now): ProfileApi {
  const ago = (days: number) => new Date(now() - days * 24 * 60 * 60 * 1000).toISOString();
  const later = (days: number) => new Date(now() + days * 24 * 60 * 60 * 1000).toISOString();
  const samples: Record<string, ClientProfile> = {
    "-NstubClient00000042": {
      nextOfKin: [
        { fullName: "Joseph Nakato", relationship: "spouse", phone: "+256701555210", idNumberMasked: "CM••••••••12AB", shareBps: 6_000 },
        { fullName: "Grace Nakato", relationship: "sibling", phone: "+256772310448", idNumberMasked: "CF••••••••77QK", shareBps: 4_000 },
      ],
      nextOfKinUpdatedAt: ago(40),
      risk: [
        {
          id: "-NstubRisk0000000042",
          answers: { purpose: "grow_savings", need_back: "1_to_3_years", early_need: "would_use_other_savings", experience: "savings_or_sacco" },
          profile: "balanced",
          answeredAt: ago(40),
          dueAgainAt: later(325),
        },
      ],
    },
  };
  return {
    async profile(clientId) {
      await new Promise((resolve) => setTimeout(resolve, 150));
      return structuredClone(samples[clientId] ?? { nextOfKin: [], nextOfKinUpdatedAt: null, risk: [] });
    },
  };
}
