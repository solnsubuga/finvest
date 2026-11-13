import { ReceiptFile, ReceiptMimeType } from "./types";

/** The largest receipt we accept. The server checks again. */
export const MAX_RECEIPT_BYTES = 10 * 1024 * 1024;

const TYPES_BY_EXTENSION: Record<string, ReceiptMimeType> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  pdf: "application/pdf",
};
const ACCEPTED = new Set<string>(Object.values(TYPES_BY_EXTENSION));

export type PickedFile = { uri: string; name: string; mimeType?: string | null; size?: number | null };

export type ReceiptProblem = "wrong_type" | "too_large" | "empty";

export const receiptProblemCopy: Record<ReceiptProblem, string> = {
  wrong_type: "That file type is not accepted. Use a JPG, PNG or PDF.",
  too_large: "That file is larger than 10 MB. Choose a smaller one.",
  empty: "That file is empty. Choose another one.",
};

/**
 * Checks a file the device handed us. The type comes from what the device
 * says, or from the file name when it says nothing. A file whose size the
 * device does not report is refused as empty, because the limit cannot be checked.
 */
export function checkReceipt(picked: PickedFile): ReceiptFile | ReceiptProblem {
  const extension = picked.name.split(".").pop()?.toLowerCase() ?? "";
  const declared = picked.mimeType?.toLowerCase() ?? "";
  const mimeType = ACCEPTED.has(declared)
    ? (declared as ReceiptMimeType)
    : declared === "" || declared === "application/octet-stream"
      ? TYPES_BY_EXTENSION[extension]
      : undefined;
  if (!mimeType) {
    return "wrong_type";
  }
  const size = picked.size ?? 0;
  if (!Number.isSafeInteger(size) || size <= 0) {
    return "empty";
  }
  if (size > MAX_RECEIPT_BYTES) {
    return "too_large";
  }
  return { uri: picked.uri, name: picked.name, mimeType, sizeBytes: size };
}

/** Whole shillings from what was typed, such as "2,000,000". Null when it is not an amount. */
export function parseShillings(text: string): number | null {
  const cleaned = text.replace(/[,\s]/g, "");
  if (!/^[1-9]\d{0,11}$/.test(cleaned)) {
    return null;
  }
  return Number(cleaned);
}

/** Groups digits as they are typed: "2000000" becomes "2,000,000". */
export function groupDigits(text: string): string {
  const digits = text.replace(/\D/g, "").replace(/^0+/, "").slice(0, 12);
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Today in Kampala (UTC+3 all year), as YYYY-MM-DD. */
export function kampalaToday(nowMs: number): string {
  return new Date(nowMs + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

/**
 * "13/11/2026" to "2026-11-13". Null when it is not a real day, is after
 * today in Kampala, or is more than 90 days old.
 */
export function parseDaySent(text: string, nowMs: number): string | null {
  const match = /^(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})$/.exec(text.trim());
  if (!match) {
    return null;
  }
  const [day, month, year] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return null;
  }
  const iso = date.toISOString().slice(0, 10);
  const today = kampalaToday(nowMs);
  const oldest = kampalaToday(nowMs - 90 * 24 * 60 * 60 * 1000);
  return iso <= today && iso >= oldest ? iso : null;
}
