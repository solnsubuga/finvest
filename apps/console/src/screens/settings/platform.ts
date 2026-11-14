/**
 * The console's view of the platform settings and the withdrawal limits on Settings.
 *
 * The shapes follow docs/platform-settings.md and the staff limits routes in docs/payouts.md
 * ("Changing the limits"). Every change takes two people. `platformHttp.ts` calls the routes;
 * `platformStub.ts` is the sample for `pnpm dev` and previews.
 */

export type Decision = "approved" | "rejected" | "withdrawn";
export type Section = "channels" | "calendar" | "staff_security" | "statement_import";

export const CHANNELS = ["mtnPayIn", "airtelPayIn", "bankPayIn", "mtnPayout", "airtelPayout", "bankPayout"] as const;
export type Channel = (typeof CHANNELS)[number];
/** True where the channel is open. A paused channel refuses new requests only. */
export type Channels = Record<Channel, boolean>;

export interface Holiday {
  /** YYYY-MM-DD. */
  date: string;
  name: string;
}

export interface OfficeNetwork {
  /** An address or a CIDR range, such as 196.43.130.0/24. */
  network: string;
  label: string;
}

export interface StatementLayout {
  /** Lower case, digits and underscores, as staff pick it on the import screen. */
  name: string;
  label: string;
  dateColumn: string;
  dateFormat: string;
  narrative: string;
  reference: string | null;
  /** Either money in and money out columns, or one amount column with a direction column and its two words. */
  moneyIn: string | null;
  moneyOut: string | null;
  amount: string | null;
  direction: string | null;
  inWord: string | null;
  outWord: string | null;
}

export interface Values {
  payment_channels: Channels;
  public_holidays: Holiday[];
  /** HH:MM, Kampala time. */
  bank_payout_cutoff: string;
  /** Days before maturity, largest first. Empty sends none. */
  maturity_reminders: number[];
  staff_idle_minutes: number;
  /** Empty allows any network. */
  office_networks: OfficeNetwork[];
  statement_layouts: StatementLayout[];
}

export type SettingKey = keyof Values;
export const SETTING_KEYS: readonly SettingKey[] = [
  "payment_channels",
  "public_holidays",
  "bank_payout_cutoff",
  "maturity_reminders",
  "staff_idle_minutes",
  "office_networks",
  "statement_layouts",
];

export interface Proposal<V> {
  id: string;
  value: V;
  reason: string;
  proposedBy: string;
  proposedAt: string;
  /** The signed-in member of staff proposed it, so they can withdraw it but not decide it. */
  yours: boolean;
}

export interface Setting<K extends SettingKey = SettingKey> {
  key: K;
  section: Section;
  value: Values[K];
  /** Null while the code's default is in use. */
  changedAt: string | null;
  changedBy: string | null;
  /** The reader may propose a change and decide other people's. */
  canChange: boolean;
  proposal: Proposal<Values[K]> | null;
}

export type SettingsMap = { [K in SettingKey]: Setting<K> };

export interface PlatformSettings {
  settings: SettingsMap;
  /** The address this console's requests come from, as the office networks see it. */
  yourAddress: string | null;
  /** How a statement layout may write its dates. */
  dateFormats: string[];
}

export interface PlatformSettingsApi {
  list(): Promise<PlatformSettings>;
  propose<K extends SettingKey>(key: K, value: Values[K], reason: string): Promise<void>;
  /** `approved` or `rejected` by someone other than the proposer, `withdrawn` by the proposer. */
  decide(proposalId: string, decision: Decision, reason: string): Promise<void>;
}

/** Amounts in shillings, the smallest unit of UGX. Null is not set. */
export interface Limits {
  /** YYYY-MM-DD. */
  effectiveFrom: string;
  mobileMoneyPerPayout: number;
  mobileMoneyPerDay: number | null;
  bankPerPayout: number | null;
  bankPerDay: number | null;
  /** A payout or a transfer to another client above this waits for two members of staff. */
  reviewAbove: number | null;
}

export interface LimitsScreen {
  inForce: Limits;
  /** Versions approved to start on a later day, soonest first. */
  upcoming: Limits[];
  change: Proposal<Limits> | null;
  canChange: boolean;
}

export interface LimitsApi {
  get(): Promise<LimitsScreen>;
  propose(limits: Limits, reason: string): Promise<void>;
  decide(changeId: string, decision: Decision, reason: string): Promise<void>;
}

export interface Problem {
  /** The last part of the backend's field path, such as `value`, `effective_from` or `reason`. */
  field: string;
  code: string;
}

export type SettingsErrorCode =
  | "invalid"
  | "conflict"
  | "leaves_you_out"
  | "day_taken"
  | "not_a_later_day"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  | "unavailable";

export class SettingsError extends Error {
  constructor(
    readonly code: SettingsErrorCode,
    /** For `invalid`: what the backend refused. */
    readonly problems: readonly Problem[] = [],
  ) {
    super(code);
    this.name = "SettingsError";
  }
}

const MESSAGES: Record<SettingsErrorCode, string> = {
  invalid: "Finvest refused this value. Fix it and send it again.",
  conflict: "Someone else proposed a change here first. Here it is as it now stands.",
  leaves_you_out: "That list leaves out the network you are on, so it would sign you out. Add your network first.",
  day_taken: "Another version of the limits already starts that day. Pick another day.",
  not_a_later_day: "The start day has come. New limits start on a later day, so propose them again.",
  same_person: "A change needs a second person. Someone other than you must decide it.",
  forbidden: "Your role can see this but not change it.",
  not_found: "This change has already been decided. Here is the setting as it now stands.",
  not_connected: "Settings are not connected to the Finvest service yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function settingsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof SettingsError ? error.code : "unavailable"];
}

const PROBLEM_TEXT: Record<string, string> = {
  required: "Fill this in.",
  invalid: "Finvest did not accept this value.",
  too_many: "That is more than Finvest allows.",
  listed_twice: "One of these is listed twice.",
  same_day_twice: "Two holidays are on the same day.",
  same_name_twice: "Two layouts have the same name.",
  built_in: "That name is the built-in layout. Pick another.",
  outside_banking_hours: "Pick a time between 08:00 and 17:00.",
  too_wide: "That network is too wide. Use /8 or narrower for IPv4, /32 or narrower for IPv6.",
  money_columns: "Give money in and money out columns, or one amount column with a direction column and its two words.",
  column_used_twice: "One column is used for two things.",
  same_as_in_word: "The word for money out must differ from the word for money in.",
  limits_invalid: "A daily limit cannot be below the limit for one payout.",
  not_a_later_day: "Pick a later day than today.",
  day_taken: "Another version already starts that day.",
};

export function problemText(problem: Problem): string {
  return PROBLEM_TEXT[problem.code] ?? "Finvest did not accept this. Change it and try again.";
}

const notConnected = () => Promise.reject(new SettingsError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedPlatformSettingsApi: PlatformSettingsApi = {
  list: notConnected,
  propose: notConnected,
  decide: notConnected,
};

export const notConnectedLimitsApi: LimitsApi = {
  get: notConnected,
  propose: notConnected,
  decide: notConnected,
};

/** The same bounds as the backend's catalogue (`platform_settings/domain/catalogue.py`). */
export const BOUNDS = {
  holidays: 60,
  holidayName: 80,
  reminders: 3,
  daysBefore: 30,
  idleMin: 5,
  idleMax: 240,
  networks: 20,
  networkLabel: 80,
  layouts: 10,
  column: 60,
  layoutLabel: 80,
  reason: 500,
  /** A mobile money wallet takes at most UGX 5,000,000 in one transfer. */
  mobileMoneyPerPayout: 5_000_000,
  amount: 10_000_000_000,
} as const;

const SHILLINGS = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** "UGX 5,000,000", or "Not set". */
export function shillings(amount: number | null): string {
  return amount === null ? "Not set" : `UGX ${SHILLINGS.format(amount)}`;
}

const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** "Fri 25 Dec 2026". */
export function formatDay(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

/** "7 days and 1 day before", or "None". */
export function remindersText(days: readonly number[]): string {
  if (days.length === 0) return "None";
  const parts = [...days].sort((a, b) => b - a).map((day) => (day === 1 ? "1 day" : `${day} days`));
  const last = parts.pop();
  return `${parts.length > 0 ? `${parts.join(", ")} and ` : ""}${last} before`;
}

export const CHANNEL_LABELS: Record<Channel, string> = {
  mtnPayIn: "MTN Mobile Money, paying in",
  airtelPayIn: "Airtel Money, paying in",
  bankPayIn: "Bank transfer, paying in",
  mtnPayout: "MTN Mobile Money, payouts",
  airtelPayout: "Airtel Money, payouts",
  bankPayout: "Bank payouts",
};

/** Whether an address falls in a network, for the "this leaves you out" warning. IPv4 only; the backend decides. */
export function inNetwork(address: string, network: string): boolean {
  const [base, bits] = network.includes("/") ? network.split("/") : [network, "32"];
  const toNumber = (ip: string) => {
    const parts = ip.split(".").map(Number);
    if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return null;
    return parts.reduce((total, part) => total * 256 + part, 0);
  };
  const a = toNumber(address);
  const b = toNumber(base ?? "");
  const prefix = Number(bits);
  if (a === null || b === null || !Number.isInteger(prefix) || prefix < 0 || prefix > 32) return address === network;
  const size = 2 ** (32 - prefix);
  return Math.floor(a / size) === Math.floor(b / size);
}
