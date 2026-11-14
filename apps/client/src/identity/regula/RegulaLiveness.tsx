import { useEffect, useRef } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import {
  FaceSDK,
  LivenessConfig,
  LivenessErrorCode,
  LivenessSkipStep,
  type LivenessResponse,
} from "@regulaforensics/face-sdk";

import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

import { finished, type RegulaLivenessProps, type RegulaOutcome } from "./types";

/**
 * Regula's liveness on a phone, with Regula's Face SDK (its own full-screen capture and
 * guidance). The SDK talks to Regula's Face service directly; the app reports only the
 * transaction ID it ends with, and the API reads the result from Regula.
 */
export function RegulaLiveness({ tag, serviceUrl, onEnd }: RegulaLivenessProps) {
  const ended = useRef(onEnd);
  useEffect(() => {
    ended.current = onEnd;
  });

  useEffect(() => {
    let gone = false;
    const end = (outcome: RegulaOutcome) => {
      if (!gone) ended.current(outcome);
    };
    void run(tag, serviceUrl).then(end, () => end({ kind: "trouble", reason: "failed" }));
    return () => {
      gone = true;
    };
  }, [tag, serviceUrl]);

  return (
    <View style={styles.fill} testID="regula-liveness">
      <ActivityIndicator accessibilityLabel="Opening the live selfie" color={colors.white} />
      <Txt color={colors.white}>Opening the live selfie</Txt>
    </View>
  );
}

async function run(tag: string, serviceUrl: string): Promise<RegulaOutcome> {
  const sdk = FaceSDK.instance;
  sdk.serviceUrl = serviceUrl;
  if (!(await sdk.isInitialized())) {
    // Without a licence file: the licence is on Regula's service.
    const [ready] = await sdk.initialize();
    if (!ready) return { kind: "trouble", reason: "failed" };
  }
  const response = await sdk.startLiveness({
    config: new LivenessConfig({
      tag,
      // One session per attempt: the API takes one transaction for it.
      attemptsCount: 1,
      // Finvest's own screen says how it went, once the API has read the result.
      skipStep: [LivenessSkipStep.SUCCESS_STEP],
      locationTrackingEnabled: false,
      // "Powered by Regula" on Finvest's own screen is enough; no Regula footer on the capture.
      copyright: false,
      preventScreenRecording: true,
    }),
  });
  return outcomeOf(response);
}

export function outcomeOf(response: LivenessResponse): RegulaOutcome {
  const error = response.error;
  if (!error) return finished(response.transactionId);
  switch (error.code) {
    case LivenessErrorCode.CANCELLED:
      return { kind: "cancelled" };
    case LivenessErrorCode.CAMERA_NO_PERMISSION:
      return { kind: "trouble", reason: "no_permission" };
    case LivenessErrorCode.CAMERA_NOT_AVAILABLE:
      return { kind: "trouble", reason: "no_camera" };
    case LivenessErrorCode.API_CALL_FAILED:
      return { kind: "trouble", reason: "connection" };
    default:
      // A session that ran and was judged still has a transaction for the API to read.
      return response.transactionId ? finished(response.transactionId) : { kind: "trouble", reason: "failed" };
  }
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: colors.black, alignItems: "center", justifyContent: "center", gap: 12 },
});
