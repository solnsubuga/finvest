import { Platform } from "react-native";
import Constants from "expo-constants";

/** What the phone says about push for this app. "unsupported" on the web, where there is none. */
export type PushPermission = "granted" | "undetermined" | "denied" | "unsupported";

/** The phone's side of push: permission and the Expo push token. Tests pass their own. */
export interface PushDevice {
  platform: "ios" | "android" | null;
  permission(): Promise<PushPermission>;
  /** Shows the phone's own prompt, which it shows only once. After that it answers at once. */
  ask(): Promise<PushPermission>;
  /** The Expo push token, `ExponentPushToken[...]`. Fails when the build has no Expo project. */
  token(): Promise<string>;
}

type Notifications = typeof import("expo-notifications");

function load(): Notifications {
  // Required on use, so the web bundle and the tests never load the native module.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require("expo-notifications") as Notifications;
}

function toPermission(status: string): PushPermission {
  return status === "granted" ? "granted" : status === "denied" ? "denied" : "undetermined";
}

/** The Expo project the push token belongs to, set by EAS in app config. */
function projectId(): string | undefined {
  const extra = Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined;
  return extra?.eas?.projectId ?? Constants.easConfig?.projectId ?? undefined;
}

export const phonePushDevice: PushDevice = {
  platform: Platform.OS === "ios" || Platform.OS === "android" ? Platform.OS : null,
  async permission() {
    if (Platform.OS !== "ios" && Platform.OS !== "android") return "unsupported";
    return toPermission((await load().getPermissionsAsync()).status);
  },
  async ask() {
    if (Platform.OS !== "ios" && Platform.OS !== "android") return "unsupported";
    return toPermission((await load().requestPermissionsAsync()).status);
  },
  async token() {
    const id = projectId();
    if (!id) {
      throw new Error("no_expo_project");
    }
    return (await load().getExpoPushTokenAsync({ projectId: id })).data;
  },
};
