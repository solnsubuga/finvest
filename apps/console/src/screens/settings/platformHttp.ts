/** The platform settings and the withdrawal limits, over the real API (docs/platform-settings.md, docs/payouts.md). */

import { ApiError, type Http } from "@/api/http.ts";
import {
  SettingsError,
  type Channels,
  type Limits,
  type LimitsApi,
  type PlatformSettingsApi,
  type Problem,
  type Proposal,
  type Setting,
  type SettingKey,
  type SettingsMap,
  type StatementLayout,
  type Values,
} from "./platform.ts";

interface ChannelsJson {
  mtn_pay_in: boolean;
  airtel_pay_in: boolean;
  bank_pay_in: boolean;
  mtn_payout: boolean;
  airtel_payout: boolean;
  bank_payout: boolean;
}

interface LayoutJson {
  name: string;
  label: string;
  date_column: string;
  date_format: string;
  narrative: string;
  reference?: string | null;
  money_in?: string | null;
  money_out?: string | null;
  amount?: string | null;
  direction?: string | null;
  in_word?: string | null;
  out_word?: string | null;
}

interface ProposalJson {
  id: string;
  value: unknown;
  reason: string;
  proposed_by: string;
  proposed_at: string;
  yours: boolean;
}

interface SettingJson {
  key: SettingKey;
  section: Setting["section"];
  value: unknown;
  changed_at: string | null;
  changed_by: string | null;
  can_change: boolean;
  proposal: ProposalJson | null;
}

interface LimitsJson {
  effective_from: string;
  currency: "UGX";
  mobile_money_per_payout_minor: number;
  mobile_money_per_day_minor: number | null;
  bank_per_payout_minor: number | null;
  bank_per_day_minor: number | null;
  review_above_minor: number | null;
}

interface LimitsChangeJson {
  id: string;
  limits: LimitsJson;
  reason: string;
  proposed_by: string;
  proposed_at: string;
  yours: boolean;
}

const channelsIn = (json: ChannelsJson): Channels => ({
  mtnPayIn: json.mtn_pay_in,
  airtelPayIn: json.airtel_pay_in,
  bankPayIn: json.bank_pay_in,
  mtnPayout: json.mtn_payout,
  airtelPayout: json.airtel_payout,
  bankPayout: json.bank_payout,
});

const channelsOut = (value: Channels): ChannelsJson => ({
  mtn_pay_in: value.mtnPayIn,
  airtel_pay_in: value.airtelPayIn,
  bank_pay_in: value.bankPayIn,
  mtn_payout: value.mtnPayout,
  airtel_payout: value.airtelPayout,
  bank_payout: value.bankPayout,
});

const layoutIn = (json: LayoutJson): StatementLayout => ({
  name: json.name,
  label: json.label,
  dateColumn: json.date_column,
  dateFormat: json.date_format,
  narrative: json.narrative,
  reference: json.reference ?? null,
  moneyIn: json.money_in ?? null,
  moneyOut: json.money_out ?? null,
  amount: json.amount ?? null,
  direction: json.direction ?? null,
  inWord: json.in_word ?? null,
  outWord: json.out_word ?? null,
});

const layoutOut = (layout: StatementLayout): LayoutJson => ({
  name: layout.name,
  label: layout.label,
  date_column: layout.dateColumn,
  date_format: layout.dateFormat,
  narrative: layout.narrative,
  reference: layout.reference,
  money_in: layout.moneyIn,
  money_out: layout.moneyOut,
  amount: layout.amount,
  direction: layout.direction,
  in_word: layout.inWord,
  out_word: layout.outWord,
});

/** The value as the console holds it. Only channels and layouts change shape. */
export function valueIn<K extends SettingKey>(key: K, json: unknown): Values[K] {
  if (key === "payment_channels") return channelsIn(json as ChannelsJson) as Values[K];
  if (key === "statement_layouts") return (json as LayoutJson[]).map(layoutIn) as Values[K];
  return json as Values[K];
}

export function valueOut<K extends SettingKey>(key: K, value: Values[K]): unknown {
  if (key === "payment_channels") return channelsOut(value as Channels);
  if (key === "statement_layouts") return (value as StatementLayout[]).map(layoutOut);
  return value;
}

function proposalIn<V>(json: Omit<ProposalJson, "value">, value: V): Proposal<V> {
  return { id: json.id, value, reason: json.reason, proposedBy: json.proposed_by, proposedAt: json.proposed_at, yours: json.yours };
}

export function toSettings(list: readonly SettingJson[]): SettingsMap {
  const map: Partial<Record<SettingKey, Setting>> = {};
  for (const json of list) {
    map[json.key] = {
      key: json.key,
      section: json.section,
      value: valueIn(json.key, json.value),
      changedAt: json.changed_at,
      changedBy: json.changed_by,
      canChange: json.can_change,
      proposal: json.proposal ? proposalIn(json.proposal, valueIn(json.key, json.proposal.value)) : null,
    } as Setting;
  }
  return map as SettingsMap;
}

export const limitsIn = (json: LimitsJson): Limits => ({
  effectiveFrom: json.effective_from,
  mobileMoneyPerPayout: json.mobile_money_per_payout_minor,
  mobileMoneyPerDay: json.mobile_money_per_day_minor,
  bankPerPayout: json.bank_per_payout_minor,
  bankPerDay: json.bank_per_day_minor,
  reviewAbove: json.review_above_minor,
});

function problems(error: ApiError): Problem[] {
  return error.fields.map((problem) => {
    const path = problem.field_path.map(String);
    // Pydantic names the field under the key of the discriminated body, as in ["body", "public_holidays", "value", 0, "date"].
    const field = path.find((part) => part === "value" || part === "reason" || part === "effective_from") ?? path[path.length - 1] ?? "value";
    const code = problem.code === "missing" || problem.code === "string_too_short" ? "required" : problem.code;
    return { field, code };
  });
}

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toSettingsError(error: unknown, deciding = false): SettingsError {
  if (error instanceof SettingsError) return error;
  if (!(error instanceof ApiError)) return new SettingsError("unavailable");
  const field = error.fields[0]?.code;
  switch (error.code) {
    case "validation_failed":
      return new SettingsError("invalid", problems(error));
    case "conflict":
      if (field === "leaves_you_out" || field === "day_taken" || field === "not_a_later_day") return new SettingsError(field);
      return new SettingsError("conflict");
    case "confirm_it_is_you":
      return new SettingsError("not_confirmed");
    case "forbidden":
      return new SettingsError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new SettingsError("not_found");
    default:
      return new SettingsError("unavailable");
  }
}

export function createHttpPlatformSettingsApi(http: Http): PlatformSettingsApi {
  return {
    async list() {
      try {
        const json = await http.request<{ settings: SettingJson[]; your_address: string | null; date_formats: string[] }>(
          "GET",
          "/staff/settings/platform",
        );
        return { settings: toSettings(json.settings), yourAddress: json.your_address, dateFormats: json.date_formats };
      } catch (error) {
        throw toSettingsError(error);
      }
    },
    async propose(key, value, reason) {
      try {
        await http.request("POST", "/staff/settings/platform/proposals", { key, value: valueOut(key, value), reason: reason.trim() });
      } catch (error) {
        throw toSettingsError(error);
      }
    },
    async decide(proposalId, decision, reason) {
      try {
        await http.request("POST", `/staff/settings/platform/proposals/${encodeURIComponent(proposalId)}/decision`, {
          decision,
          reason: reason.trim(),
        });
      } catch (error) {
        throw toSettingsError(error, decision !== "withdrawn");
      }
    },
  };
}

export function createHttpLimitsApi(http: Http): LimitsApi {
  return {
    async get() {
      try {
        const json = await http.request<{ in_force: LimitsJson; upcoming: LimitsJson[]; change: LimitsChangeJson | null; can_change: boolean }>(
          "GET",
          "/staff/withdrawal-limits",
        );
        return {
          inForce: limitsIn(json.in_force),
          upcoming: json.upcoming.map(limitsIn),
          change: json.change ? proposalIn(json.change, limitsIn(json.change.limits)) : null,
          canChange: json.can_change,
        };
      } catch (error) {
        throw toSettingsError(error);
      }
    },
    async propose(limits, reason) {
      try {
        await http.request("POST", "/staff/withdrawal-limits/changes", {
          effective_from: limits.effectiveFrom,
          mobile_money_per_payout_minor: limits.mobileMoneyPerPayout,
          mobile_money_per_day_minor: limits.mobileMoneyPerDay,
          bank_per_payout_minor: limits.bankPerPayout,
          bank_per_day_minor: limits.bankPerDay,
          review_above_minor: limits.reviewAbove,
          reason: reason.trim(),
        });
      } catch (error) {
        throw toSettingsError(error);
      }
    },
    async decide(changeId, decision, reason) {
      try {
        await http.request("POST", `/staff/withdrawal-limits/changes/${encodeURIComponent(changeId)}/decision`, {
          decision,
          reason: reason.trim(),
        });
      } catch (error) {
        throw toSettingsError(error, decision !== "withdrawn");
      }
    },
  };
}
