import type { Topic } from "./api.ts";

export const TOPIC_LABELS: Record<Topic, string> = {
  deposit: "Deposit",
  withdrawal: "Withdrawal",
  investment: "Investment",
  identity: "Identity",
  account: "Account",
  other: "Other",
};

export const TOPICS = Object.keys(TOPIC_LABELS) as Topic[];

/** "4 min", "1 h", "2 days": how long a client has waited. */
export function waited(since: string, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - Date.parse(since)) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day" : `${days} days`;
}

/** "15:02", Kampala time. */
export function clock(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Kampala", hour: "2-digit", minute: "2-digit" }).format(
    new Date(iso),
  );
}

/** "10 Oct 2026" for a calendar date. */
export function longDate(date: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" }).format(
    new Date(`${date}T00:00:00Z`),
  );
}
