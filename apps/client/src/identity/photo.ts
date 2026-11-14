import { PhotoFile, PhotoMimeType } from "./types";

/** The largest photo the API takes. It checks again. */
export const MAX_PHOTO_BYTES = 10 * 1024 * 1024;

const TYPES_BY_EXTENSION: Record<string, PhotoMimeType> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
};
const ACCEPTED = new Set<string>(Object.values(TYPES_BY_EXTENSION));

export type PickedPhoto = { uri: string; name: string; mimeType?: string | null; size?: number | null };

export type PhotoProblem = "wrong_type" | "too_large" | "empty";

export const photoProblemCopy: Record<PhotoProblem, string> = {
  wrong_type: "That file type is not accepted. Use a JPG, PNG or WebP photo.",
  too_large: "That photo is larger than 10 MB. Take it again, or choose a smaller one.",
  empty: "That photo is empty. Take it again.",
};

/**
 * Checks a photo the device handed us. The type comes from what the device
 * says, or from the file name when it says nothing. A photo whose size the
 * device does not report is refused as empty, because the limit cannot be checked.
 */
export function checkPhoto(picked: PickedPhoto): PhotoFile | PhotoProblem {
  const extension = picked.name.split(".").pop()?.toLowerCase() ?? "";
  const declared = picked.mimeType?.toLowerCase() ?? "";
  const mimeType = ACCEPTED.has(declared)
    ? (declared as PhotoMimeType)
    : declared === "" || declared === "application/octet-stream"
      ? TYPES_BY_EXTENSION[extension]
      : undefined;
  if (!mimeType) return "wrong_type";
  const size = picked.size ?? 0;
  if (size <= 0) return "empty";
  if (size > MAX_PHOTO_BYTES) return "too_large";
  return { uri: picked.uri, name: picked.name, mimeType, size };
}

/** Today in Kampala (UTC+3 all year), "YYYY-MM-DD". */
export function kampalaToday(now: number = Date.now()): string {
  return new Date(now + 3 * 3_600_000).toISOString().slice(0, 10);
}
