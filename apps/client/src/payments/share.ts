import { Share } from "react-native";
import * as Clipboard from "expo-clipboard";

export type ShareOutcome = "shared" | "copied" | "failed";

/**
 * Opens the device's share sheet with the text. A browser without one gets
 * the text copied instead, so the button always does something.
 */
export async function shareText(title: string, message: string): Promise<ShareOutcome> {
  try {
    await Share.share({ title, message });
    return "shared";
  } catch {
    try {
      await Clipboard.setStringAsync(message);
      return "copied";
    } catch {
      return "failed";
    }
  }
}

/** The lines of a receipt or of bank details, as "Label: value", under a heading. */
export function asText(heading: string, rows: { label: string; value: string }[]): string {
  return [heading, ...rows.map((row) => `${row.label}: ${row.value}`)].join("\n");
}
