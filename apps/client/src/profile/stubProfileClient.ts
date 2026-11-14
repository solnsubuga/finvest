import {
  Closure,
  NextOfKin,
  Person,
  ProfileClient,
  ProfileError,
  RiskAnswers,
  RiskProfile,
} from "./types";

/**
 * An in-memory stand-in for the profile routes. Nothing here is real.
 * `ProfileProvider.tsx` keeps it out of production builds.
 *
 * The two demo accounts start with the canvas's next of kin, and with
 * investments still running, so their account cannot close yet. An account
 * made by signing up starts with nothing, so it can. A number ending in 99
 * cannot reach the service. The stub refuses what the backend refuses.
 */

const SEEDED = new Set(["+256772123456", "+256701000200"]);
const QUESTIONS: RiskProfile["questions"] = [
  { code: "purpose", options: ["emergency_fund", "planned_purchase", "grow_savings", "retirement_or_children"] },
  { code: "need_back", options: ["within_3_months", "3_to_12_months", "1_to_3_years", "over_3_years"] },
  { code: "early_need", options: ["would_withdraw_early", "would_use_other_savings", "would_wait"] },
  { code: "experience", options: ["none", "savings_or_sacco", "bills_or_bonds", "shares_or_unit_trusts"] },
];
const POINTS: Record<string, number[]> = {
  purpose: [0, 1, 2, 3],
  need_back: [0, 1, 2, 3],
  early_need: [0, 2, 3],
  experience: [0, 1, 2, 2],
};
const ID_NUMBER = /^[A-Z0-9]{5,32}$/;

function phoneOf(value: string): string | null {
  const digits = value.replace(/[\s().-]/g, "");
  if (/^0\d{9}$/.test(digits)) return `+256${digits.slice(1)}`;
  if (/^256\d{9}$/.test(digits)) return `+${digits}`;
  if (/^\+\d{9,15}$/.test(digits)) return digits;
  return null;
}

export function createStubProfileClient(account: string | null): ProfileClient {
  const seeded = account !== null && SEEDED.has(account);
  let kin: NextOfKin = seeded
    ? {
        people: [
          { fullName: "Joseph Nakato", relationship: "spouse", phone: "+256701555210", idNumber: "CM90012345ABCD", shareBps: 6000 },
          { fullName: "Grace Nakato", relationship: "sibling", phone: "+256772310448", idNumber: "CF91000000WXYZ", shareBps: 4000 },
        ],
        updatedAt: "2026-10-02T09:12:00Z",
      }
    : { people: [], updatedAt: null };
  let latest: RiskProfile["latest"] = null;
  let closure: Closure = {
    standing: seeded
      ? {
          runningInvestments: 2,
          owed: [{ minor: 8_164_760, currency: "UGX" }],
          openDeposits: 0,
          openPayouts: 0,
          openGoals: 2,
          livePlans: 0,
          blockers: ["investments_running", "money_owed", "goals_open"],
        }
      : { runningInvestments: 0, owed: [], openDeposits: 0, openPayouts: 0, openGoals: 0, livePlans: 0, blockers: [] },
    request: null,
  };

  const reach = async () => {
    await new Promise((resolve) => setTimeout(resolve, 120));
    if (!account || account.endsWith("99")) throw new ProfileError("unavailable");
  };

  const risk = (): RiskProfile => ({ questions: QUESTIONS, latest, due: latest === null });

  return {
    async getNextOfKin() {
      await reach();
      return kin;
    },
    async saveNextOfKin(people: Person[]) {
      await reach();
      const fields: { field: string; code: string }[] = [];
      const cleaned = people.map((p, index) => {
        const phone = phoneOf(p.phone);
        const idNumber = p.idNumber.replace(/[\s-]/g, "").toUpperCase();
        const fullName = p.fullName.trim().split(/\s+/).join(" ");
        if (fullName.split(" ").length < 2) fields.push({ field: `people.${index}.full_name`, code: "full_name_needed" });
        if (!phone) fields.push({ field: `people.${index}.phone`, code: "invalid_phone" });
        else if (phone === account) fields.push({ field: `people.${index}.phone`, code: "your_own_number" });
        if (!ID_NUMBER.test(idNumber)) fields.push({ field: `people.${index}.id_number`, code: "invalid_id_number" });
        if (p.shareBps < 1 || p.shareBps > 10_000) fields.push({ field: `people.${index}.share_bps`, code: "invalid_share" });
        return { ...p, fullName, phone: phone ?? p.phone, idNumber };
      });
      if (people.length > 5) fields.push({ field: "people", code: "too_many" });
      if (people.length && people.reduce((sum, p) => sum + p.shareBps, 0) !== 10_000) {
        fields.push({ field: "people", code: "shares_must_total_100" });
      }
      if (fields.length) throw new ProfileError("invalid", fields);
      kin = { people: cleaned, updatedAt: new Date().toISOString() };
      return kin;
    },
    async getRiskProfile() {
      await reach();
      return risk();
    },
    async answerRiskQuestions(answers: RiskAnswers) {
      await reach();
      let total = 0;
      for (const q of QUESTIONS) {
        const at = q.options.indexOf(answers[q.code] ?? "");
        if (at < 0) throw new ProfileError("invalid", [{ field: `answers.${q.code}`, code: "required" }]);
        total += POINTS[q.code]?.[at] ?? 0;
      }
      const profile =
        answers.need_back === "within_3_months" || total <= 3 ? "careful" : total <= 7 ? "balanced" : "long_term";
      latest = {
        answers,
        profile,
        longestTermDays: profile === "careful" ? 182 : profile === "balanced" ? 364 : null,
        answeredAt: new Date().toISOString(),
      };
      return risk();
    },
    async getClosure() {
      await reach();
      return closure;
    },
    async askToClose(reason) {
      await reach();
      if (closure.standing.blockers.length) {
        throw new ProfileError("blocked", closure.standing.blockers.map((code) => ({ field: "blockers", code })));
      }
      if (closure.request && ["requested", "prepared"].includes(closure.request.status)) {
        throw new ProfileError("conflict");
      }
      closure = {
        ...closure,
        request: { id: "-NstubClosure0000001", status: "requested", reason, requestedAt: new Date().toISOString(), declineNote: null },
      };
      return closure;
    },
    async cancelClosure(requestId) {
      await reach();
      if (closure.request?.id !== requestId) throw new ProfileError("conflict");
      closure = { ...closure, request: { ...closure.request, status: "cancelled" } };
      return closure;
    },
    async downloadMyData() {
      await reach();
    },
  };
}
