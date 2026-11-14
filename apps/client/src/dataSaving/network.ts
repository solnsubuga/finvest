import * as Network from "expo-network";

import { Connection } from "./types";

/** What the phone says it is connected through. Anything it cannot name is "unknown". */
export async function currentConnection(): Promise<Connection> {
  try {
    const state = await Network.getNetworkStateAsync();
    switch (state.type) {
      case Network.NetworkStateType.WIFI:
      case Network.NetworkStateType.ETHERNET:
        return "wifi";
      case Network.NetworkStateType.CELLULAR:
        return "mobile";
      case Network.NetworkStateType.NONE:
        return "offline";
      default:
        return "unknown";
    }
  } catch {
    return "unknown";
  }
}

/** A phone always says which network it is on. */
export function canTellConnection(): boolean {
  return true;
}
