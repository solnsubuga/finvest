import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";

/**
 * On a phone: write the CSV to the app's cache, open the share sheet so the
 * client can save or send it, then remove the cached copy.
 */
export async function saveCsv(fileName: string, csv: string): Promise<void> {
  const file = new File(Paths.cache, fileName);
  file.create({ overwrite: true });
  file.write(csv);
  try {
    await Sharing.shareAsync(file.uri, { mimeType: "text/csv", dialogTitle: fileName, UTI: "public.comma-separated-values-text" });
  } finally {
    try {
      file.delete();
    } catch {
      // Already gone. The cache is cleared by the system in time anyway.
    }
  }
}
