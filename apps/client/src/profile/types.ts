/**
 * The contract between the client's profile screens and the backend:
 * next of kin and the investing questions (docs/client-profile.md), closing
 * the account (docs/account-closure.md) and a copy of the client's data.
 */

import { Money } from "@/payments/types";

export type Relationship =
  | "spouse"
  | "child"
  | "parent"
  | "sibling"
  | "other_relative"
  | "friend"
  | "other";

export const RELATIONSHIPS: { value: Relationship; label: string }[] = [
  { value: "spouse", label: "Husband or wife" },
  { value: "child", label: "Child" },
  { value: "parent", label: "Parent" },
  { value: "sibling", label: "Brother or sister" },
  { value: "other_relative", label: "Other relative" },
  { value: "friend", label: "Friend" },
  { value: "other", label: "Someone else" },
];

export type Person = {
  fullName: string;
  relationship: Relationship;
  /** E.164 once saved. */
  phone: string;
  idNumber: string;
};

export type NextOfKin = { people: Person[]; updatedAt: string | null };

export type Profile = "careful" | "balanced" | "long_term";

export type RiskAnswers = Record<string, string>;

export type RiskProfile = {
  /** The question codes in order, each with its answer codes. */
  questions: { code: string; options: string[] }[];
  latest: { answers: RiskAnswers; profile: Profile; longestTermDays: number | null; answeredAt: string } | null;
  /** True before the first answers, and once a year has passed. */
  due: boolean;
};

export type Blocker =
  | "investments_running"
  | "money_owed"
  | "deposits_in_progress"
  | "payouts_in_progress"
  | "goals_open"
  | "plans_live";

export type Standing = {
  runningInvestments: number;
  owed: Money[];
  openDeposits: number;
  openPayouts: number;
  openGoals: number;
  livePlans: number;
  blockers: Blocker[];
};

export type ClosureStatus = "requested" | "prepared" | "closed" | "declined" | "cancelled";

export type Closure = {
  standing: Standing;
  request: {
    id: string;
    status: ClosureStatus;
    reason: string | null;
    requestedAt: string;
    declineNote: string | null;
  } | null;
};

export type ProfileErrorCode =
  /** The request never reached Finvest, or no answer came back. */
  | "unavailable"
  /** Too many tries in a short time. */
  | "rate_limited"
  /** Finvest answered, but refused for a reason the screen has no words of its own for. */
  | "refused"
  | "invalid"
  | "blocked"
  | "sign_in_again"
  | "conflict";

export class ProfileError extends Error {
  readonly code: ProfileErrorCode;
  /** For "invalid": each problem by dotted field, such as `people.1.phone`, and its code. */
  readonly fields: { field: string; code: string }[];

  constructor(code: ProfileErrorCode, fields: { field: string; code: string }[] = []) {
    super(code);
    this.name = "ProfileError";
    this.code = code;
    this.fields = fields;
  }
}

export interface ProfileClient {
  getNextOfKin(): Promise<NextOfKin>;
  saveNextOfKin(people: Person[]): Promise<NextOfKin>;
  getRiskProfile(): Promise<RiskProfile>;
  answerRiskQuestions(answers: RiskAnswers): Promise<RiskProfile>;
  getClosure(): Promise<Closure>;
  askToClose(reason: string | null): Promise<Closure>;
  cancelClosure(requestId: string): Promise<Closure>;
  /** Saves the copy of the client's data to the device or the browser's downloads. */
  downloadMyData(): Promise<void>;
}

export function describeProfileError(error: unknown): string {
  if (error instanceof ProfileError) {
    switch (error.code) {
      case "sign_in_again":
        return "For your safety, log in again before doing this.";
      case "conflict":
        return "Something changed meanwhile. Go back and try again.";
      case "blocked":
        return "Your account still has things to finish first. They are listed above.";
      case "invalid":
        return "Some details need fixing. They are marked below.";
      case "rate_limited":
        return "Too many tries. Wait a few minutes and try again.";
      case "refused":
        return "Finvest could not do this just now. Try again later, or ask us in Help.";
      default:
        break;
    }
  }
  return "We could not reach Finvest. Check your connection and try again.";
}

/**
 * What the screens understand from a refusal that has only a code and an HTTP status, as a
 * file download gets. With no status the request never reached Finvest.
 */
export function refusalFor(code: string | undefined, status: number | null): ProfileError {
  if (status === null) return new ProfileError("unavailable");
  if (code === "sign_in_again") return new ProfileError("sign_in_again");
  if (code === "rate_limited" || status === 429) return new ProfileError("rate_limited");
  return new ProfileError("refused");
}
