import { ReactNode, useCallback, useEffect, useState } from "react";
import { Platform, StyleSheet, View } from "react-native";

import { api } from "@/api/http";
import { Connection, currentConnection, onConnection, reportConnection } from "@/api/connection";

import { MaintenanceScreen } from "./MaintenanceScreen";
import { OfflineBanner } from "./OfflineBanner";

/** How often the app asks again while it has no connection, and while Finvest is being updated. */
export const OFFLINE_RETRY_MS = 15_000;
export const MAINTENANCE_RETRY_MS = 30_000;

/** A cheap read that is switched off with the rest of the client side during maintenance. Any answer, even "sign in first", proves the API is back. */
async function probe(): Promise<void> {
  try {
    await api("GET /client/products", { timeoutMs: 10_000 });
  } catch {
    // http.ts has already reported what it heard.
  }
}

/**
 * Shows the offline banner over every screen while the API cannot be reached, and the
 * maintenance screen while Finvest is being updated. Both go away by themselves the moment a
 * request is answered again. Nothing is retried on a screen's behalf, and no money moves here.
 */
export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [connection, setConnection] = useState<Connection>(currentConnection);
  const [checking, setChecking] = useState(false);

  useEffect(() => onConnection(setConnection), []);

  // The demo build has no API to switch off, so `?maintenance=on` shows the screen on purpose.
  useEffect(() => {
    if (process.env.EXPO_PUBLIC_DEMO_AUTH !== "1" || typeof window === "undefined") return;
    if (window.location?.search?.includes("maintenance=on")) reportConnection("maintenance");
  }, []);

  // A browser says when it loses and regains its network, so the banner can show at once.
  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined") return;
    const offline = () => reportConnection("offline");
    const online = () => void probe();
    window.addEventListener("offline", offline);
    window.addEventListener("online", online);
    if (window.navigator?.onLine === false) offline();
    return () => {
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", online);
    };
  }, []);

  useEffect(() => {
    if (connection === "online") return;
    const timer = setInterval(() => void probe(), connection === "offline" ? OFFLINE_RETRY_MS : MAINTENANCE_RETRY_MS);
    return () => clearInterval(timer);
  }, [connection]);

  const retry = useCallback(async () => {
    setChecking(true);
    await probe();
    setChecking(false);
  }, []);

  return (
    <View style={styles.fill}>
      {children}
      {connection === "maintenance" ? (
        <View style={StyleSheet.absoluteFill}>
          <MaintenanceScreen checking={checking} onRetry={retry} />
        </View>
      ) : null}
      {connection === "offline" ? <OfflineBanner checking={checking} onRetry={retry} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({ fill: { flex: 1 } });
