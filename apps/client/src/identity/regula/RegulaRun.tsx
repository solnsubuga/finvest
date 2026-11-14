import { useEffect, useRef } from "react";
import { StyleSheet, View } from "react-native";
import { randomUUID } from "expo-crypto";

import { Button, TextLink } from "@/components/Button";
import { Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

import { RegulaLiveness } from "./RegulaLiveness";
import { regulaFaceUrl, type RegulaOutcome } from "./types";

/** Written inline, so a production build leaves the demo's stand-in out of reach. */
const demo = process.env.EXPO_PUBLIC_DEMO_AUTH === "1";

/** Whether this build can run Regula's liveness: it knows Regula's Face service, or is the demo. */
export function regulaCanRun(): boolean {
  return regulaFaceUrl() !== null || demo;
}

/** Runs one session of Regula's liveness for an attempt's tag. */
export function RegulaRun({ tag, onEnd }: { tag: string; onEnd: (outcome: RegulaOutcome) => void }) {
  const url = regulaFaceUrl();
  if (url) return <RegulaLiveness tag={tag} serviceUrl={url} onEnd={onEnd} />;
  if (demo) return <DemoRegula onEnd={onEnd} />;
  return <CannotRun onEnd={onEnd} />;
}

function CannotRun({ onEnd }: { onEnd: (outcome: RegulaOutcome) => void }) {
  const ended = useRef(onEnd);
  useEffect(() => {
    ended.current({ kind: "trouble", reason: "not_supported" });
  }, []);
  return null;
}

/**
 * The demo has no Regula service, so this stands in for Regula's capture: it ends the session
 * as Regula would, with a transaction for the demo API to "read".
 */
function DemoRegula({ onEnd }: { onEnd: (outcome: RegulaOutcome) => void }) {
  return (
    <View style={styles.demo} testID="regula-demo">
      <Txt weight="bold" color={colors.white} center>
        {"Regula's live selfie runs here"}
      </Txt>
      <Txt size={fontSizes.sm} color={colors.white} center>
        {
          "On a real build, Regula's own camera screen opens here and asks you to move your face as it shows. This demo has no Regula service, so it stands in."
        }
      </Txt>
      <View style={{ gap: 10, alignSelf: "stretch" }}>
        <Button
          label="Finish the live selfie"
          variant="lime"
          onPress={() => onEnd({ kind: "done", transactionId: randomUUID() })}
          testID="regula-demo-finish"
        />
        <TextLink label="Close it" onDark onPress={() => onEnd({ kind: "cancelled" })} testID="regula-demo-close" />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  demo: { flex: 1, backgroundColor: colors.black, padding: 24, gap: 16, alignItems: "center", justifyContent: "center" },
});
