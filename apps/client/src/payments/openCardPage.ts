import { Linking, Platform } from "react-native";

/**
 * Sends the client to the card provider's page, the only place a card is typed.
 *
 * On the web the whole page goes there, never a frame, so card entry stays entirely on the
 * provider's page and Pesapal brings the client back to our return page. On a phone the page
 * opens in the system browser, and the client comes back to the app themselves, which reads the
 * payment again when it returns to the front.
 */
export function openCardPage(url: string): void {
  if (Platform.OS === "web") {
    window.location.assign(url);
    return;
  }
  void Linking.openURL(url);
}
