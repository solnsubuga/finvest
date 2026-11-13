import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";

import { API_URL } from "@/api/config";
import { readToken } from "@/api/token";

/**
 * On a phone: download the PDF with the session token into the app's cache,
 * open the share sheet so the client can save or send it, then remove the
 * cached copy, so a statement does not stay on the device unasked.
 */
export async function savePdf(path: string, fileName: string): Promise<void> {
  const token = await readToken();
  const target = new File(Paths.cache, fileName);
  const file = await File.downloadFileAsync(`${API_URL}${path}`, target, {
    headers: token ? { Authorization: `Bearer ${token}`, Accept: "application/pdf" } : { Accept: "application/pdf" },
    idempotent: true,
  });
  try {
    await Sharing.shareAsync(file.uri, { mimeType: "application/pdf", UTI: "com.adobe.pdf", dialogTitle: fileName });
  } finally {
    try {
      file.delete();
    } catch {
      // Already gone. The cache is cleared by the system in time anyway.
    }
  }
}
