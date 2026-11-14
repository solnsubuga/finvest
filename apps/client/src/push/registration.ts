import { api } from "@/api/http";

import { phonePushDevice, PushDevice, PushPermission } from "./device";

/**
 * Registering this phone for push alerts, as docs/notifications.md describes:
 * the token goes to `PUT /client/push-devices` after each sign-in, and to
 * `POST /client/push-devices/remove` at sign-out. It travels in the body only,
 * so it never sits in an address or an access log.
 */

export type PushState =
  /** This phone gets push alerts for the signed-in client. */
  | { status: "on" }
  /** The client has not been asked yet, or said "not now". */
  | { status: "off" }
  /** The client said no in the phone's prompt. Only the phone's Settings can change it. */
  | { status: "blocked" }
  /** A browser, the demo, or a build without an Expo project. */
  | { status: "unsupported" }
  /** The phone said yes but the token could not be sent. Asked again at the next sign-in. */
  | { status: "failed" };

/** The token this app registered for the signed-in client, kept for sign-out. Memory only. */
let registered: string | null = null;

let device: PushDevice = phonePushDevice;

/** Tests swap the phone for a fake. */
export function setPushDevice(next: PushDevice | null): void {
  device = next ?? phonePushDevice;
  registered = null;
}

export function registeredPushToken(): string | null {
  return registered;
}

function settled(permission: PushPermission): PushState | null {
  if (permission === "unsupported") return { status: "unsupported" };
  if (permission === "denied") return { status: "blocked" };
  if (permission === "undetermined") return { status: "off" };
  return null;
}

/**
 * Sends this phone's token to the API when push is allowed. With `ask`, the
 * phone's prompt is shown first if it has not been answered. Never throws.
 */
export async function registerPush({ ask }: { ask: boolean }): Promise<PushState> {
  const platform = device.platform;
  if (!platform) {
    return { status: "unsupported" };
  }
  try {
    let permission = await device.permission();
    if (permission === "undetermined" && ask) {
      permission = await device.ask();
    }
    const early = settled(permission);
    if (early) return early;
    let token: string;
    try {
      token = await device.token();
    } catch {
      // No Expo project in this build: push cannot work here at all.
      return { status: "unsupported" };
    }
    await api("PUT /client/push-devices", { body: { token, platform } });
    registered = token;
    return { status: "on" };
  } catch {
    return { status: "failed" };
  }
}

/** What push looks like on this phone now, without asking or sending anything. */
export async function pushState(): Promise<PushState> {
  if (!device.platform) {
    return { status: "unsupported" };
  }
  if (registered) {
    return { status: "on" };
  }
  try {
    // Allowed but not sent for this client yet: "Turn on" sends it without a prompt.
    return settled(await device.permission()) ?? { status: "off" };
  } catch {
    return { status: "failed" };
  }
}

/**
 * Stops push to this phone for the signed-in client, before the session ends.
 * Best effort: signing out goes ahead whatever the API says.
 */
export async function forgetPush(): Promise<void> {
  const token = registered;
  registered = null;
  if (!token) {
    return;
  }
  try {
    await api("POST /client/push-devices/remove", { body: { token } });
  } catch {
    // The next client to register this phone takes it over anyway.
  }
}
