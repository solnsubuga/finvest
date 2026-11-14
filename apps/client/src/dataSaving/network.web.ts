import { Connection } from "./types";

type NetworkInformation = { type?: string };

function info(): NetworkInformation | undefined {
  if (typeof navigator === "undefined") {
    return undefined;
  }
  return (navigator as Navigator & { connection?: NetworkInformation }).connection;
}

/**
 * Only some browsers, mostly Chrome on Android, say whether they are on Wi-Fi
 * or mobile data. The rest give "unknown", and a download is then not held.
 */
export async function currentConnection(): Promise<Connection> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return "offline";
  }
  switch (info()?.type) {
    case "wifi":
    case "ethernet":
      return "wifi";
    case "cellular":
      return "mobile";
    default:
      return "unknown";
  }
}

export function canTellConnection(): boolean {
  return typeof info()?.type === "string";
}
