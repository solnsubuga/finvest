export type Notifications = typeof import("expo-notifications");

/** Required on use, so the tests never load the native module. notifications.web.ts has none. */
export function loadNotifications(): Notifications {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require("expo-notifications") as Notifications;
}
