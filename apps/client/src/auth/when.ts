const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Finvest keeps Kampala time, three hours ahead of UTC all year. */
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

function two(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

function dayNumber(ms: number): number {
  return Math.floor((ms + KAMPALA_OFFSET_MS) / DAY_MS);
}

/**
 * When something happened on an account, in Kampala time: "today at 14:05",
 * "yesterday at 09:10", "3 Nov at 18:00", with the year only when it is not
 * this one.
 */
export function accountWhen(iso: string, nowMs: number): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) {
    return "";
  }
  const at = new Date(ms + KAMPALA_OFFSET_MS);
  const time = `${two(at.getUTCHours())}:${two(at.getUTCMinutes())}`;
  const age = dayNumber(nowMs) - dayNumber(ms);
  if (age <= 0) {
    return `today at ${time}`;
  }
  if (age === 1) {
    return `yesterday at ${time}`;
  }
  const thisYear = new Date(nowMs + KAMPALA_OFFSET_MS).getUTCFullYear();
  const day = `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]}`;
  return `${at.getUTCFullYear() === thisYear ? day : `${day} ${at.getUTCFullYear()}`} at ${time}`;
}
