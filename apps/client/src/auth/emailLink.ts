import { useState } from "react";
import { Platform } from "react-native";
import * as Linking from "expo-linking";

/**
 * The token from an email link, which carries it after the `#` so it never
 * reaches a server log: https://app.finvestug.com/verify-email#<token>.
 * Null when the address has none.
 */
export function tokenFromLink(url: string | null | undefined): string | null {
  if (!url) {
    return null;
  }
  const at = url.indexOf("#");
  if (at < 0) {
    return null;
  }
  const token = url.slice(at + 1).trim();
  // The server's tokens are URL-safe base64. Anything else is not one of ours.
  return /^[A-Za-z0-9_-]{16,200}$/.test(token) ? token : null;
}

/**
 * The token of the link that opened the app: undefined while a phone is still
 * being handed the link, null when there is none. On the web it is read once,
 * because the screen then wipes it from the address bar.
 */
export function useLinkToken(): string | null | undefined {
  const url = Linking.useURL();
  // Read once, before the address bar is wiped.
  const [webToken] = useState(() =>
    Platform.OS === "web" && typeof window !== "undefined" ? tokenFromLink(window.location.href) : undefined,
  );
  if (Platform.OS === "web") {
    return webToken ?? null;
  }
  // On a phone the link arrives a moment after the screen opens.
  return url === null ? undefined : tokenFromLink(url);
}

