import { createElement, useEffect, useRef, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";

import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

import { finished, type RegulaLivenessProps, type RegulaOutcome, type RegulaTrouble } from "./types";

/**
 * Regula's liveness in a browser, with Regula's `<face-liveness>` web component (its own
 * capture and guidance, WebAssembly in a worker). The component talks to Regula's Face service
 * directly; the app reports only the transaction ID it ends with, and the API reads the result
 * from Regula. The component's script is served by the app from /regula
 * (scripts/face-assets.mjs) and loads only on this screen.
 */

const SCRIPT = "/regula/face-components.js";
/** How long the component may take to arrive before the client is told it did not open. */
const LOAD_WITHIN_MS = 20_000;

let loading: Promise<void> | null = null;

function loadComponent(): Promise<void> {
  if (typeof customElements !== "undefined" && customElements.get("face-liveness")) return Promise.resolve();
  if (!loading) {
    loading = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("face-liveness did not load")), LOAD_WITHIN_MS);
      const script = document.createElement("script");
      script.src = SCRIPT;
      script.onload = () => {
        clearTimeout(timer);
        if (customElements.get("face-liveness")) resolve();
        else void customElements.whenDefined("face-liveness").then(() => resolve());
      };
      script.onerror = () => {
        clearTimeout(timer);
        script.remove();
        reject(new Error("face-liveness did not load"));
      };
      document.head.appendChild(script);
    });
    // A failed load can be tried again on the next visit.
    loading.catch(() => {
      loading = null;
    });
  }
  return loading;
}

type Detail = {
  action?: string;
  data?: { status?: number; reason?: string; response?: { transactionId?: string } } | null;
};

type FaceLivenessElement = HTMLElement & { settings: Record<string, unknown> };

/** Reasons the component gives, as the copy the app shows. */
const TROUBLE: Record<string, RegulaTrouble> = {
  CAMERA_PERMISSION_DENIED: "no_permission",
  NO_CAMERA: "no_camera",
  INCORRECT_CAMERA_ID: "no_camera",
  CAMERA_UNKNOWN_ERROR: "no_camera",
  NOT_SUPPORTED: "not_supported",
  HTTP_NOT_SUPPORTED: "not_supported",
  WASM_ERROR: "not_supported",
  WEBSERVICE_NOT_COMPATIBLE: "failed",
  CONNECTION_ERROR: "connection",
};

/** What one of the component's events means for the attempt, or null while it is still running. */
export function outcomeOf(detail: Detail): RegulaOutcome | null {
  const { action, data } = detail;
  if (action === "CLOSE") return { kind: "cancelled" };
  if (action === "RETRY_COUNTER_EXCEEDED") return { kind: "trouble", reason: "failed" };
  if (action !== "PROCESS_FINISHED" || !data) return null;
  if (data.status === 1) return finished(data.response?.transactionId);
  if (data.reason === "CANCELLED") return { kind: "cancelled" };
  return { kind: "trouble", reason: TROUBLE[data.reason ?? ""] ?? "failed" };
}

export function RegulaLiveness({ tag, serviceUrl, onEnd }: RegulaLivenessProps) {
  const holder = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  const ended = useRef(onEnd);
  useEffect(() => {
    ended.current = onEnd;
  });

  useEffect(() => {
    let gone = false;
    loadComponent().then(
      () => {
        if (!gone) setReady(true);
      },
      () => {
        if (!gone) ended.current({ kind: "trouble", reason: "not_supported" });
      },
    );
    return () => {
      gone = true;
    };
  }, []);

  useEffect(() => {
    const box = holder.current;
    if (!ready || !box) return;
    let done = false;
    const element = document.createElement("face-liveness") as FaceLivenessElement;
    const listen = (event: Event) => {
      const outcome = outcomeOf((event as CustomEvent<Detail>).detail ?? {});
      if (!outcome || done) return;
      done = true;
      ended.current(outcome);
    };
    element.addEventListener("face-liveness", listen);
    box.appendChild(element);
    // Settings take effect only once the element is in the page.
    element.settings = {
      url: serviceUrl,
      tag,
      locale: "en",
      // One session per attempt: the API takes one transaction for it. Finvest's own screen
      // says how it went, once the API has read the result.
      finishScreen: false,
      retryCount: 1,
      changeCamera: false,
      // "Powered by Regula" on Finvest's own screen is enough; no Regula footer on the capture.
      copyright: false,
      customization: {
        fontFamily: "inherit",
        onboardingScreenStartButtonBackground: colors.black,
        onboardingScreenStartButtonBackgroundHover: colors.ink,
        onboardingScreenStartButtonTitle: colors.white,
        onboardingScreenStartButtonTitleHover: colors.white,
        cameraScreenSectorActive: colors.lime,
        cameraScreenSectorTarget: colors.border,
        cameraScreenStrokeNormal: colors.lime,
        processingScreenProgress: colors.black,
      },
    };
    return () => {
      element.removeEventListener("face-liveness", listen);
      element.remove();
    };
  }, [ready, tag, serviceUrl]);

  return (
    <View style={styles.fill} testID="regula-liveness">
      {ready ? null : (
        <View style={styles.loading}>
          <ActivityIndicator accessibilityLabel="Opening the live selfie" color={colors.black} />
          <Txt color={colors.muted}>Opening the live selfie</Txt>
        </View>
      )}
      {createElement("div", { ref: holder, style: { width: "100%", height: "100%" } })}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: colors.surface },
  loading: { position: "absolute", top: 0, right: 0, bottom: 0, left: 0, alignItems: "center", justifyContent: "center", gap: 12 },
});
