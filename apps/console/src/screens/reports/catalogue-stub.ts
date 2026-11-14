/**
 * Sample reports and schedules for `pnpm dev` and preview builds, checked the way the API checks
 * them. Nothing here is real: the people are the stub team and the files hold made-up rows.
 */

import { STUB_ROLE_PERMISSIONS, STUB_STAFF } from "@/auth/stub.ts";
import type { Permission } from "@/auth/types.ts";
import {
  addDays,
  MAX_RECIPIENTS,
  monthPeriod,
  periodProblem,
  quarterOf,
  quarterPeriod,
  reportFilename,
  ReportsError,
  toDay,
  type Frequency,
  type PeriodKind,
  type Report,
  type ReportKey,
  type ReportsApi,
  type Schedule,
} from "./catalogue.ts";

interface Definition {
  key: ReportKey;
  name: string;
  about: string;
  period: PeriodKind;
  permission: Permission;
  byCurrency: boolean;
}

const DEFINITIONS: Definition[] = [
  { key: "daily_position", name: "Daily position", about: "Client money by account and currency at the end of a day, with the cash in the trust account and the ioTec wallet.", period: "day", permission: "finance.view", byCurrency: true },
  { key: "deposits_and_withdrawals", name: "Deposits and withdrawals", about: "Every deposit and payout asked for in the period, by channel, with how it ended and how long it took.", period: "days", permission: "money_movement.view", byCurrency: true },
  { key: "interest_and_tax", name: "Interest and tax withheld", about: "Interest each investment earned in the month and the tax withheld from it, which is the tax to pay URA.", period: "month", permission: "finance.view", byCurrency: true },
  { key: "maturity_ladder", name: "Maturity ladder", about: "What matures each day, how much is set to pay out, and how much rolls over.", period: "ahead", permission: "maturities.view", byCurrency: true },
  { key: "identity_and_risk", name: "Identity and risk", about: "Clients by identity check and account state, and the screening and monitoring cases, as they stand now.", period: "now", permission: "compliance.view", byCurrency: false },
  { key: "client_statements", name: "Client statements", about: "Every client's monthly statement for a month: made, nothing to show, or failed.", period: "month", permission: "clients.view", byCurrency: false },
  { key: "complaints_quarterly", name: "Complaints for the regulator", about: "The quarter's complaints by category: received, resolved, upheld, past their deadline, and the average time to resolve.", period: "quarter", permission: "complaints.view", byCurrency: false },
];

const ALLOWED: Record<PeriodKind, Frequency[]> = {
  day: ["daily"],
  days: ["daily", "weekly", "monthly"],
  month: ["monthly"],
  quarter: ["quarterly"],
  ahead: ["daily", "weekly"],
  now: ["daily", "weekly", "monthly"],
};

function mayDownload(definition: Definition, permissions: readonly Permission[]): boolean {
  return permissions.includes("reports.view") && permissions.includes(definition.permission);
}

function defaultPeriod(kind: PeriodKind, today: string): { from: string; to: string } {
  const yesterday = addDays(today, -1);
  if (kind === "now") return { from: today, to: today };
  if (kind === "day") return { from: yesterday, to: yesterday };
  if (kind === "days") return { from: addDays(today, -6), to: today };
  if (kind === "month") return monthPeriod(addDays(`${today.slice(0, 7)}-01`, -1).slice(0, 7)) ?? { from: today, to: today };
  if (kind === "quarter") {
    const { year, quarter } = quarterOf(today);
    return quarter === 1 ? quarterPeriod(year - 1, 4) : quarterPeriod(year, quarter - 1);
  }
  return { from: today, to: addDays(today, 27) };
}

/** Today in Kampala, which is UTC+3 all year. */
function kampalaToday(now: number): string {
  return toDay(new Date(now + 3 * 60 * 60 * 1000));
}

interface Me {
  id: string;
  name: string;
  permissions: readonly Permission[];
}

export interface StubOptions {
  /** No schedules at all. */
  empty?: boolean;
}

const NAMES = new Map(STUB_STAFF.map((person) => [person.staffId, person.name]));
const ROLES = new Map(STUB_STAFF.map((person) => [person.staffId, person.role]));
// The invitee has not finished signing up, so is not active.
const ACTIVE = STUB_STAFF.filter((person) => person.staffId !== "stub-invited");

function permissionsOf(staffId: string): readonly Permission[] {
  const role = ROLES.get(staffId);
  return role ? STUB_ROLE_PERMISSIONS[role] : [];
}

let counter = 0;
const newId = () => `-NstubSchedule${String(++counter).padStart(6, "0")}`;

export function createStubReportsApi(me: Me, now: () => number = Date.now, options: StubOptions = {}): ReportsApi {
  const mine = DEFINITIONS.filter((definition) => mayDownload(definition, me.permissions));
  const mineKeys = new Set(mine.map((definition) => definition.key));
  const start = now();
  const hoursAgo = (hours: number) => new Date(start - hours * 3_600_000).toISOString();

  const seed = (partial: Pick<Schedule, "reportKey" | "frequency" | "currency" | "recipients" | "reason" | "status" | "proposedBy"> & Partial<Schedule>): Schedule => {
    const definition = DEFINITIONS.find((found) => found.key === partial.reportKey) as Definition;
    return {
      id: newId(),
      reportName: definition.name,
      recipientNames: partial.recipients.map((id) => NAMES.get(id) ?? ""),
      proposedByName: NAMES.get(partial.proposedBy) ?? null,
      proposedAt: hoursAgo(50),
      decidedBy: null,
      decidedAt: null,
      decisionReason: null,
      stoppedBy: null,
      stoppedAt: null,
      lastSentAt: null,
      yours: partial.proposedBy === me.id,
      ...partial,
    };
  };

  const schedules: Schedule[] = options.empty
    ? []
    : [
        seed({ reportKey: "daily_position", frequency: "daily", currency: "UGX", recipients: ["stub-finance", "stub-admin"], reason: "Finance reads it before the morning call", status: "active", proposedBy: "stub-finance", proposedAt: hoursAgo(60), decidedBy: "stub-admin", decidedAt: hoursAgo(48), decisionReason: "Agreed with Finance", lastSentAt: hoursAgo(4) }),
        seed({ reportKey: "deposits_and_withdrawals", frequency: "weekly", currency: "UGX", recipients: ["stub-operations"], reason: "Weekly look at failed payouts", status: "proposed", proposedBy: "stub-operations", proposedAt: hoursAgo(3) }),
        seed({ reportKey: "identity_and_risk", frequency: "monthly", currency: null, recipients: ["stub-compliance"], reason: "Monthly compliance review", status: "active", proposedBy: "stub-compliance", proposedAt: hoursAgo(330), decidedBy: "stub-admin", decidedAt: hoursAgo(300), decisionReason: "Needed for the board pack", lastSentAt: hoursAgo(310) }),
        seed({ reportKey: "maturity_ladder", frequency: "daily", currency: "UGX", recipients: ["stub-finance"], reason: "Cash planning", status: "stopped", proposedBy: "stub-finance", proposedAt: hoursAgo(720), decidedBy: "stub-admin", decidedAt: hoursAgo(700), decisionReason: "Fine", stoppedBy: "stub-finance", stoppedAt: hoursAgo(200), lastSentAt: hoursAgo(210) }),
      ];
  const answered = new Map<string, Schedule>();

  const find = (id: string): Schedule => {
    const found = schedules.find((schedule) => schedule.id === id && mineKeys.has(schedule.reportKey));
    if (!found) throw new ReportsError("not_found");
    return found;
  };
  const refused = (reason: string) => new ReportsError("refused", reason);

  return {
    async catalogue() {
      const today = kampalaToday(now());
      const reports: Report[] = mine.map((definition) => ({
        key: definition.key,
        name: definition.name,
        about: definition.about,
        period: definition.period,
        byCurrency: definition.byCurrency,
        ...defaultPeriod(definition.period, today),
      }));
      return { today, reports };
    },
    async download(key, period) {
      const definition = mine.find((found) => found.key === key);
      if (!definition) throw new ReportsError("forbidden");
      const problem = periodProblem(definition.period, period.from, period.to, kampalaToday(now()));
      if (problem) throw new ReportsError("period", null);
      const currency = definition.byCurrency ? period.currency : null;
      const rows = ["report,date_from,date_to,currency,note", `${key},${period.from},${period.to},${currency ?? ""},sample rows from the stub`];
      return { file: new Blob([`${rows.join("\r\n")}\r\n`], { type: "text/csv" }), filename: reportFilename(key, currency, period.from, period.to) };
    },
    async schedules() {
      const order = (schedule: Schedule) => (schedule.status === "proposed" ? 0 : schedule.status === "active" ? 1 : 2);
      const shown = schedules
        .filter((schedule) => mineKeys.has(schedule.reportKey))
        .sort((left, right) => order(left) - order(right) || right.proposedAt.localeCompare(left.proposedAt));
      return {
        schedules: shown.map((schedule) => ({ ...schedule })),
        frequencies: Object.fromEntries(mine.map((definition) => [definition.key, ALLOWED[definition.period]])),
        people: ACTIVE.map((person) => ({
          id: person.staffId,
          name: person.name,
          reports: mine.filter((definition) => mayDownload(definition, STUB_ROLE_PERMISSIONS[person.role])).map((definition) => definition.key),
        })).filter((person) => person.reports.length > 0),
      };
    },
    async propose(proposal, idempotencyKey) {
      const earlier = answered.get(idempotencyKey);
      if (earlier) return { ...earlier };
      const definition = mine.find((found) => found.key === proposal.reportKey);
      if (!definition) throw new ReportsError("forbidden");
      const reason = proposal.reason.trim();
      if (!reason) throw refused("reason_required");
      if (!ALLOWED[definition.period].includes(proposal.frequency)) throw refused("frequency_not_allowed");
      const recipients = [...new Set(proposal.recipients)].sort();
      if (recipients.length === 0 || recipients.length > MAX_RECIPIENTS) throw refused("recipients_count");
      for (const id of recipients) {
        if (!ACTIVE.some((person) => person.staffId === id)) throw refused("recipient_not_active");
        if (!mayDownload(definition, permissionsOf(id))) throw refused("recipient_may_not_download");
      }
      const schedule: Schedule = {
        ...seed({ reportKey: definition.key, frequency: proposal.frequency, currency: definition.byCurrency ? proposal.currency : null, recipients, reason, status: "proposed", proposedBy: me.id }),
        proposedByName: me.name,
        proposedAt: new Date(now()).toISOString(),
      };
      schedules.push(schedule);
      answered.set(idempotencyKey, schedule);
      return { ...schedule };
    },
    async decide(id, decision, reason, idempotencyKey) {
      const earlier = decision === "approve" ? answered.get(idempotencyKey) : undefined;
      if (earlier) return { ...earlier };
      const schedule = find(id);
      if (!reason.trim()) throw refused("reason_required");
      const at = new Date(now()).toISOString();
      if (decision === "stop") {
        if (schedule.status !== "active") throw refused("not_running");
        Object.assign(schedule, { status: "stopped", stoppedBy: me.id, stoppedAt: at });
        return { ...schedule };
      }
      if (schedule.status !== "proposed") throw refused("not_waiting");
      if (decision === "withdraw") {
        if (!schedule.yours) throw new ReportsError("refused", "not_proposed_by_you");
        Object.assign(schedule, { status: "withdrawn", decidedBy: me.id, decidedAt: at, decisionReason: reason.trim() });
        return { ...schedule };
      }
      if (schedule.yours) throw refused("proposed_by_you");
      Object.assign(schedule, { status: decision === "approve" ? "active" : "declined", decidedBy: me.id, decidedAt: at, decisionReason: reason.trim() });
      if (decision === "approve") answered.set(idempotencyKey, schedule);
      return { ...schedule };
    },
  };
}
