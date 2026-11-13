const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Finvest keeps Kampala time, three hours ahead of UTC all year. */
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

/** The Kampala wall clock for an instant, read through the UTC getters. */
function kampala(ms: number): Date {
  return new Date(ms + KAMPALA_OFFSET_MS);
}

function dayNumber(ms: number): number {
  return Math.floor((ms + KAMPALA_OFFSET_MS) / DAY_MS);
}

function two(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

export function clockTime(iso: string): string {
  const at = kampala(Date.parse(iso));
  return `${two(at.getUTCHours())}:${two(at.getUTCMinutes())}`;
}

/** "13 Nov", with the year only when it is not this one. */
function dayAndMonth(ms: number, nowMs: number): string {
  const at = kampala(ms);
  const text = `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]}`;
  return at.getUTCFullYear() === kampala(nowMs).getUTCFullYear()
    ? text
    : `${text} ${at.getUTCFullYear()}`;
}

/** How long ago, as short as the notification list needs: "15:32", "Tue", "1 Nov". */
export function shortWhen(iso: string, nowMs: number): string {
  const ms = Date.parse(iso);
  const age = dayNumber(nowMs) - dayNumber(ms);
  if (age <= 0) {
    return clockTime(iso);
  }
  if (age < 7) {
    return DAYS[kampala(ms).getUTCDay()] as string;
  }
  return dayAndMonth(ms, nowMs);
}

/** The heading over a day of chat: "Today", "Yesterday", "Tue 10 Nov". */
export function dayHeading(iso: string, nowMs: number): string {
  const ms = Date.parse(iso);
  const age = dayNumber(nowMs) - dayNumber(ms);
  if (age <= 0) {
    return "Today";
  }
  if (age === 1) {
    return "Yesterday";
  }
  return `${DAYS[kampala(ms).getUTCDay()]} ${dayAndMonth(ms, nowMs)}`;
}

/** True when two instants fall on the same Kampala day. */
export function sameDay(first: string, second: string): boolean {
  return dayNumber(Date.parse(first)) === dayNumber(Date.parse(second));
}

/** "Fri 20 Nov" for a `YYYY-MM-DD` date, which has no time or zone. */
export function plainDate(date: string, nowMs: number): string {
  const ms = Date.parse(`${date}T00:00:00Z`);
  const at = new Date(ms);
  const text = `${DAYS[at.getUTCDay()]} ${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]}`;
  return at.getUTCFullYear() === kampala(nowMs).getUTCFullYear()
    ? text
    : `${text} ${at.getUTCFullYear()}`;
}

/** "13 Nov" for an instant. */
export function dateOf(iso: string, nowMs: number): string {
  return dayAndMonth(Date.parse(iso), nowMs);
}
