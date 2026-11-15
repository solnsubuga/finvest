/**
 * Regula's liveness check, run in the app with Regula's own capture and guidance
 * (docs/identity-checks.md#regula-liveness). The API hands out an attempt whose page is
 * `regula-liveness:<tag>`; the app runs Regula's liveness with that tag and reports the
 * transaction ID it ends with. The API reads the result from Regula itself.
 */

/** How the API marks an attempt that runs in the app instead of on a page. */
export const REGULA_PAGE = "regula-liveness:";

/** A transaction ID as Regula gives it, and as the API takes it: a UUID. */
export const TRANSACTION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The tag of an attempt that runs Regula's liveness in the app, or null for any other page. */
export function regulaTag(url: string | null): string | null {
  if (!url?.startsWith(REGULA_PAGE)) return null;
  const tag = url.slice(REGULA_PAGE.length);
  return /^[A-Za-z0-9_-]{1,127}$/.test(tag) ? tag : null;
}

/**
 * Regula's Face service, which the SDK talks to while it runs: Regula's cloud address from the
 * contract, the same one as the API's FINVEST_REGULA_FACE_URL. Null when the build has none,
 * and then the selfie is taken with the camera. Only https.
 */
export function regulaFaceUrl(): string | null {
  const url = process.env.EXPO_PUBLIC_REGULA_FACE_URL?.trim();
  if (!url) return null;
  try {
    return new URL(url).protocol === "https:" ? url.replace(/\/+$/, "") : null;
  } catch {
    return null;
  }
}

/**
 * How a session of Regula's liveness ended:
 * - `done`: it finished and gave a transaction to report. Whether it saw a live person is for
 *   the API to read from Regula, never for the phone to say.
 * - `cancelled`: the client closed it.
 * - `trouble`: it could not run or finish. `reason` says why, for the copy shown.
 */
export type RegulaOutcome =
  | { kind: "done"; transactionId: string }
  | { kind: "cancelled" }
  | { kind: "trouble"; reason: RegulaTrouble };

export type RegulaTrouble = "no_permission" | "no_camera" | "not_supported" | "connection" | "failed";

export type RegulaLivenessProps = {
  /** The attempt's tag, so the API can check the session is this attempt's. */
  tag: string;
  /** Regula's Face service. */
  serviceUrl: string;
  /** Called once, when the session ends. */
  onEnd: (outcome: RegulaOutcome) => void;
};

/** Copy for each trouble, as a notice on the screen the client comes back to. */
export const regulaTroubleCopy: Record<RegulaTrouble, string> = {
  no_permission: "Finvest needs your camera for the live selfie. Allow it in your settings, then try again.",
  no_camera: "We could not find a camera to use. Try again, or take a selfie with the camera instead.",
  not_supported: "This browser cannot run the live selfie. Try another browser, or take a selfie with the camera instead.",
  connection: "We lost the connection during the live selfie. Check your internet, then try again.",
  failed: "The live selfie did not finish. Try again facing a window, with your whole face in view.",
};

/** Ends the outcome with a transaction only if it looks like one. */
export function finished(transactionId: string | null | undefined): RegulaOutcome {
  return transactionId && TRANSACTION_ID.test(transactionId)
    ? { kind: "done", transactionId: transactionId.toLowerCase() }
    : { kind: "trouble", reason: "failed" };
}

/** Whether this build can run Regula's liveness: it knows Regula's Face service, or is the demo. */
export function regulaCanRun(): boolean {
  return regulaFaceUrl() !== null || process.env.EXPO_PUBLIC_DEMO_AUTH === "1";
}
