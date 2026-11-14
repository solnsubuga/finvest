import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";

import { API_URL } from "@/api/config";
import { readToken } from "@/api/token";

import { ProfileError, refusalFor } from "./types";

export const DATA_PATH = "/client/data-export";

/**
 * On a phone: download the copy with the session token into the app's cache,
 * open the share sheet so the client can save it, then remove the cached
 * copy, so their details do not stay on the device unasked.
 */
export async function saveMyData(fileName: string): Promise<void> {
  const token = await readToken();
  const headers: Record<string, string> = token
    ? { Authorization: `Bearer ${token}`, Accept: "application/json" }
    : { Accept: "application/json" };
  let file: File;
  try {
    file = await File.downloadFileAsync(`${API_URL}${DATA_PATH}`, new File(Paths.cache, fileName), {
      headers,
      idempotent: true,
    });
  } catch {
    // A refusal is not saved as a file, so ask once more for its reason.
    throw await refusal(headers);
  }
  try {
    await Sharing.shareAsync(file.uri, { mimeType: "application/json", dialogTitle: fileName });
  } finally {
    try {
      file.delete();
    } catch {
      // Already gone. The cache is cleared by the system in time anyway.
    }
  }
}

async function refusal(headers: Record<string, string>): Promise<ProfileError> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${DATA_PATH}`, { headers });
  } catch {
    return new ProfileError("unavailable");
  }
  let code: string | undefined;
  try {
    code = ((await response.json()) as { error?: { code?: string } }).error?.code;
  } catch {
    code = undefined;
  }
  // It worked this time, so the first try failed on the way: say so, and let them try again.
  return response.ok ? new ProfileError("unavailable") : refusalFor(code, response.status);
}
