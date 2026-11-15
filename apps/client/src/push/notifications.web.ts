import type { Notifications } from "./notifications";

/**
 * A browser has no push here (device.ts answers "unsupported" first), so the web build leaves
 * expo-notifications out: about 40 KB less script before the first screen.
 */
export function loadNotifications(): Notifications {
  throw new Error("Push is not available in a browser");
}
