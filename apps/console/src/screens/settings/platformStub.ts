import {
  SettingsError,
  SETTING_KEYS,
  type Limits,
  type LimitsApi,
  type LimitsScreen,
  type PlatformSettingsApi,
  type Proposal,
  type Section,
  type Setting,
  type SettingKey,
  type SettingsMap,
  type Values,
} from "./platform.ts";

/**
 * Sample settings and limits for `pnpm dev` and previews. Not connected to Finvest. Changes stay in
 * this browser tab. The stub refuses what the backend refuses most often: deciding your own change,
 * a second change while one waits, and limits that start today.
 *
 * `sample`: approved values, with a holiday change and a limits change proposed by someone else.
 * `mine`: the same two changes, proposed by the signed-in person.
 * `empty`: every default in use and nothing waiting.
 */
export type PlatformSample = "sample" | "mine" | "empty";

const SECTIONS: Record<SettingKey, Section> = {
  payment_channels: "channels",
  public_holidays: "calendar",
  bank_payout_cutoff: "calendar",
  maturity_reminders: "calendar",
  staff_idle_minutes: "staff_security",
  office_networks: "staff_security",
  statement_layouts: "statement_import",
};

const PERMISSION: Record<Section, string> = {
  channels: "payment_channels.change",
  calendar: "calendar.change",
  staff_security: "staff_security.change",
  statement_import: "statement_layouts.change",
};

const HOLIDAYS = [
  { date: "2026-12-25", name: "Christmas Day" },
  { date: "2026-12-26", name: "Boxing Day" },
  { date: "2027-01-01", name: "New Year's Day" },
  { date: "2027-01-26", name: "Liberation Day" },
  { date: "2027-02-16", name: "Archbishop Janani Luwum Day" },
  { date: "2027-03-08", name: "International Women's Day" },
  { date: "2027-03-26", name: "Good Friday" },
  { date: "2027-03-29", name: "Easter Monday" },
];

const DEFAULTS: Values = {
  payment_channels: { mtnPayIn: true, airtelPayIn: true, bankPayIn: true, mtnPayout: true, airtelPayout: true, bankPayout: true },
  public_holidays: HOLIDAYS,
  bank_payout_cutoff: "14:00",
  maturity_reminders: [7, 1],
  staff_idle_minutes: 30,
  office_networks: [],
  statement_layouts: [],
};

const APPROVED: Partial<Values> = {
  payment_channels: { ...DEFAULTS.payment_channels, airtelPayout: false },
  staff_idle_minutes: 15,
  office_networks: [
    { network: "196.43.130.0/24", label: "Kampala office" },
    { network: "102.134.149.12", label: "Entebbe branch" },
  ],
  statement_layouts: [
    {
      name: "stanbic_csv",
      label: "Stanbic business online",
      dateColumn: "Posting Date",
      dateFormat: "DD/MM/YYYY",
      narrative: "Description",
      reference: "Reference",
      moneyIn: "Credit",
      moneyOut: "Debit",
      amount: null,
      direction: null,
      inWord: null,
      outWord: null,
    },
  ],
};

const IN_FORCE: Limits = {
  effectiveFrom: "2026-01-01",
  mobileMoneyPerPayout: 5_000_000,
  mobileMoneyPerDay: null,
  bankPerPayout: null,
  bankPerDay: null,
  reviewAbove: 10_000_000,
};

const settle = <T>(work: () => T): Promise<T> => {
  try {
    return Promise.resolve(work());
  } catch (error) {
    return Promise.reject(error);
  }
};

type Stored = Omit<Proposal<unknown>, "yours"> & { key: SettingKey };

interface Reader {
  id: string;
  permissions: readonly string[];
}

/** Today in Kampala, as YYYY-MM-DD. */
const kampalaToday = (now: number) => new Date(now + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);

export function createStubPlatformSettingsApi(reader: Reader, now: () => number = Date.now, seed: PlatformSample = "sample"): PlatformSettingsApi {
  let counter = 0;
  const other = "sample-staff-daniel";
  const values: Values = { ...DEFAULTS, ...(seed === "empty" ? {} : APPROVED) };
  const changed: Partial<Record<SettingKey, { at: string; by: string }>> =
    seed === "empty"
      ? {}
      : Object.fromEntries(Object.keys(APPROVED).map((key) => [key, { at: "2026-11-02T08:15:00Z", by: other }]));
  const waiting: Stored[] =
    seed === "empty"
      ? []
      : [
          {
            key: "public_holidays",
            id: "sample-setting-1",
            value: [...HOLIDAYS, { date: "2027-03-20", name: "Eid al-Fitr" }].sort((a, b) => a.date.localeCompare(b.date)),
            reason: "Eid al-Fitr gazetted by the Ministry of Internal Affairs for 20 March.",
            proposedBy: seed === "mine" ? reader.id : other,
            proposedAt: "2026-11-13T09:40:00Z",
          },
        ];
  const may = (key: SettingKey) => reader.permissions.includes(PERMISSION[SECTIONS[key]]);

  return {
    list: () =>
      settle(() => {
        const settings = Object.fromEntries(
          SETTING_KEYS.map((key) => {
            const proposal = waiting.find((item) => item.key === key);
            const setting: Setting = {
              key,
              section: SECTIONS[key],
              value: values[key],
              changedAt: changed[key]?.at ?? null,
              changedBy: changed[key]?.by ?? null,
              canChange: may(key),
              proposal: proposal ? { ...proposal, yours: proposal.proposedBy === reader.id } : null,
            } as Setting;
            return [key, setting];
          }),
        ) as SettingsMap;
        return { settings, yourAddress: "196.43.130.27", dateFormats: ["YYYY-MM-DD", "DD/MM/YYYY", "DD-MM-YYYY", "DD.MM.YYYY", "MM/DD/YYYY", "DD MMM YYYY", "DD-MMM-YYYY"] };
      }),
    propose: (key, value, reason) =>
      settle(() => {
        if (!may(key)) throw new SettingsError("forbidden");
        if (reason.trim().length === 0) throw new SettingsError("invalid", [{ field: "reason", code: "required" }]);
        if (waiting.some((item) => item.key === key)) throw new SettingsError("conflict");
        counter += 1;
        waiting.push({ key, id: `stub-setting-${counter}`, value, reason: reason.trim(), proposedBy: reader.id, proposedAt: new Date(now()).toISOString() });
      }),
    decide: (proposalId, decision, reason) =>
      settle(() => {
        const index = waiting.findIndex((item) => item.id === proposalId);
        const item = waiting[index];
        if (!item) throw new SettingsError("not_found");
        if (!may(item.key)) throw new SettingsError("forbidden");
        const own = item.proposedBy === reader.id;
        if (own !== (decision === "withdrawn")) throw new SettingsError("same_person");
        if (reason.trim().length === 0) throw new SettingsError("invalid", [{ field: "reason", code: "required" }]);
        waiting.splice(index, 1);
        if (decision === "approved") {
          (values as unknown as Record<SettingKey, unknown>)[item.key] = item.value;
          changed[item.key] = { at: new Date(now()).toISOString(), by: reader.id };
        }
      }),
  };
}

export function createStubLimitsApi(reader: Reader, now: () => number = Date.now, seed: PlatformSample = "sample"): LimitsApi {
  let counter = 0;
  const may = reader.permissions.includes("withdrawal_limits.change");
  const versions: Limits[] = [IN_FORCE];
  let change: Omit<Proposal<Limits>, "yours"> | null =
    seed === "empty"
      ? null
      : {
          id: "sample-limits-1",
          value: { effectiveFrom: "2026-12-01", mobileMoneyPerPayout: 3_000_000, mobileMoneyPerDay: 6_000_000, bankPerPayout: null, bankPerDay: null, reviewAbove: 8_000_000 },
          reason: "Lower mobile money limits after the November fraud review, as agreed at the risk meeting.",
          proposedBy: seed === "mine" ? reader.id : "sample-staff-daniel",
          proposedAt: "2026-11-13T10:05:00Z",
        };

  const screen = (): LimitsScreen => {
    const today = kampalaToday(now());
    const sorted = [...versions].sort((a, b) => a.effectiveFrom.localeCompare(b.effectiveFrom));
    const inForce = [...sorted].reverse().find((item) => item.effectiveFrom <= today) ?? IN_FORCE;
    return {
      inForce,
      upcoming: sorted.filter((item) => item.effectiveFrom > today),
      change: change ? { ...change, yours: change.proposedBy === reader.id } : null,
      canChange: may,
    };
  };

  return {
    get: () => settle(screen),
    propose: (limits, reason) =>
      settle(() => {
        if (!may) throw new SettingsError("forbidden");
        if (limits.effectiveFrom <= kampalaToday(now())) throw new SettingsError("invalid", [{ field: "effective_from", code: "not_a_later_day" }]);
        if (versions.some((item) => item.effectiveFrom === limits.effectiveFrom)) throw new SettingsError("day_taken");
        if (change) throw new SettingsError("conflict");
        counter += 1;
        change = { id: `stub-limits-${counter}`, value: limits, reason: reason.trim(), proposedBy: reader.id, proposedAt: new Date(now()).toISOString() };
      }),
    decide: (changeId, decision) =>
      settle(() => {
        if (!change || change.id !== changeId) throw new SettingsError("not_found");
        if (!may) throw new SettingsError("forbidden");
        if ((change.proposedBy === reader.id) !== (decision === "withdrawn")) throw new SettingsError("same_person");
        if (decision === "approved") {
          if (change.value.effectiveFrom <= kampalaToday(now())) throw new SettingsError("not_a_later_day");
          versions.push(change.value);
        }
        change = null;
      }),
  };
}
