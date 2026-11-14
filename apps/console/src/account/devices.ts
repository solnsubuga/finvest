/**
 * The devices signed in as the person using the console: `/staff/sessions` in
 * backend/app/modules/identity/sessions_api.py. A member of staff who loses a laptop, or sees a
 * device they do not know, signs it out here. Nobody else's sessions are ever listed.
 */

import { ApiError, type Http } from "@/api/http.ts";

export type SecondStep = "passkey" | "authenticator" | "code" | "signup";

export interface Device {
  id: string;
  /** A fixed label such as "Chrome on Windows". */
  device: string;
  secondStep: SecondStep;
  signedInAt: string;
  lastSeenAt: string;
  /** This browser. */
  current: boolean;
}

export interface DevicesApi {
  list(): Promise<Device[]>;
  signOut(sessionId: string): Promise<void>;
  /** Every device but this one. Answers how many were signed out. */
  signOutOthers(): Promise<number>;
}

export type DevicesErrorCode = "not_found" | "not_connected" | "unavailable";

export class DevicesError extends Error {
  readonly code: DevicesErrorCode;

  constructor(code: DevicesErrorCode) {
    super(code);
    this.name = "DevicesError";
    this.code = code;
  }
}

const MESSAGES: Record<DevicesErrorCode, string> = {
  not_found: "That device is already signed out. The list now shows where things stand.",
  not_connected: "Your devices are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function devicesErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof DevicesError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new DevicesError("not_connected"));

export const notConnectedDevicesApi: DevicesApi = { list: notConnected, signOut: notConnected, signOutOthers: notConnected };

interface DeviceJson {
  id: string;
  device: string;
  transport: "cookie" | "token";
  second_step: SecondStep;
  signed_in_at: string;
  last_seen_at: string;
  current: boolean;
}

function toDevicesError(error: unknown): DevicesError {
  if (error instanceof DevicesError) return error;
  if (error instanceof ApiError && error.code === "not_found") return new DevicesError("not_found");
  return new DevicesError("unavailable");
}

export function createHttpDevicesApi(http: Http): DevicesApi {
  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toDevicesError(error);
    }
  }
  return {
    list: () =>
      call(async () =>
        (await http.request<{ items: DeviceJson[] }>("GET", "/staff/sessions")).items.map((json) => ({
          id: json.id,
          device: json.device,
          secondStep: json.second_step,
          signedInAt: json.signed_in_at,
          lastSeenAt: json.last_seen_at,
          current: json.current,
        })),
      ),
    signOut: (sessionId) =>
      call(async () => {
        await http.request("DELETE", `/staff/sessions/${encodeURIComponent(sessionId)}`);
      }),
    signOutOthers: () => call(async () => (await http.request<{ ended: number }>("POST", "/staff/sessions/sign-out-others")).ended),
  };
}

/** Sample devices for `pnpm dev` and previews. `empty` is only this browser. */
export function createStubDevicesApi(which: "sample" | "empty" | "error" = "sample", now: () => number = Date.now, delayMs = 120): DevicesApi {
  const minute = 60_000;
  const at = (minutesAgo: number) => new Date(now() - minutesAgo * minute).toISOString();
  let devices: Device[] = [
    { id: "-NstubSession0000001", device: "Chrome on Windows", secondStep: "passkey", signedInAt: at(95), lastSeenAt: at(0), current: true },
    ...(which === "empty"
      ? []
      : [
          { id: "-NstubSession0000002", device: "Safari on Mac", secondStep: "passkey" as const, signedInAt: at(60 * 26), lastSeenAt: at(60 * 3), current: false },
          { id: "-NstubSession0000003", device: "Edge on Windows", secondStep: "authenticator" as const, signedInAt: at(60 * 24 * 4), lastSeenAt: at(60 * 24 * 2), current: false },
        ]),
  ];
  const later = <T,>(value: () => T) =>
    new Promise<T>((resolve, reject) =>
      setTimeout(() => {
        if (which === "error") return reject(new DevicesError("unavailable"));
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs),
    );
  return {
    list: () => later(() => devices.map((device) => ({ ...device }))),
    signOut: (sessionId) =>
      later(() => {
        if (!devices.some((device) => device.id === sessionId && !device.current)) throw new DevicesError("not_found");
        devices = devices.filter((device) => device.id !== sessionId);
      }),
    signOutOthers: () =>
      later(() => {
        const ended = devices.filter((device) => !device.current).length;
        devices = devices.filter((device) => device.current);
        return ended;
      }),
  };
}
