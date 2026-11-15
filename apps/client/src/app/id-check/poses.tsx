import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { useClaimClient } from "@/claim/ClaimProvider";
import { handInIdCheck } from "@/claim/handInIdCheck";
import { ClaimError } from "@/claim/types";
import { FaceReading, movedAngle } from "@/identity/guidance";
import { GuidedCapture } from "@/identity/GuidedCapture";
import { useIdentity } from "@/identity/IdentityProvider";
import { StatusLine, StepTop } from "@/identity/Parts";
import { headTurns, OPPOSITE, Pose, POSES, poseCopy, posesMissing } from "@/identity/poses";
import { IdentityCheck, IdentityError, PhotoFile } from "@/identity/types";
import { useIdentityCheck } from "@/identity/useIdentityCheck";
import { colors, fontSizes } from "@/theme";

/** Whether the selfie, and so its head turns, may be taken now. Mirrors the API's rule. */
function selfieOpen(check: IdentityCheck): boolean {
  return check.status === "draft" || (check.status === "awaiting_client" && check.retakePhotos.includes("selfie"));
}

/**
 * ID check: the guided selfie's head turns, one after another (docs/identity-checks.md#guided-selfie).
 * Starts with what they are; each pose then has the live camera. Once all four are kept, a draft
 * is handed in, as the selfie step did before.
 */
export default function HeadTurns() {
  const state = useIdentityCheck();

  if (state.status !== "ready") {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Head turns" }} />
        <BackButton fallback="/id-check" />
        <View style={{ marginTop: 24 }}>
          {state.status === "loading" ? <Loading label="Loading your ID check" /> : <LoadFailed message={state.message} onRetry={state.reload} />}
        </View>
      </Screen>
    );
  }
  const check = state.check;
  if (!check) return <Redirect href="/id-check/document" />;
  if (!headTurns(check) || !selfieOpen(check)) return <Redirect href="/id-check" />;
  if (check.missing.includes("selfie") || (check.status === "awaiting_client" && !check.photos.includes("selfie"))) {
    return <Redirect href="/id-check/photo/selfie" />;
  }
  return <Turns key={check.id} check={check} />;
}

function Turns({ check: first }: { check: IdentityCheck }) {
  const router = useRouter();
  const { client, markRetaken } = useIdentity();
  const claims = useClaimClient();
  const [check, setCheck] = useState(first);
  // With all four kept already, the client came back to take them again.
  const [queue, setQueue] = useState<Pose[]>(() => {
    const missing = posesMissing(first);
    return missing.length > 0 ? missing : [...POSES];
  });
  const [started, setStarted] = useState(false);
  // What the phone measured for each frame taken in this visit, so a pair goes opposite ways.
  const [measured, setMeasured] = useState<Partial<Record<Pose, number>>>({});
  const pose = queue[0] ?? null;

  const finish = useCallback(
    async (updated: IdentityCheck) => {
      if (updated.status === "awaiting_client") {
        markRetaken(updated.id, "selfie");
        router.replace("/id-check");
        return;
      }
      try {
        await handInIdCheck(client, claims);
      } catch (caught) {
        // Whatever went wrong, the status screen reads the check again and offers to send it.
        if (!(caught instanceof IdentityError || caught instanceof ClaimError)) throw caught;
      }
      router.replace("/id-check");
    },
    [client, claims, markRetaken, router],
  );

  const send = useCallback(
    async (file: PhotoFile, reading: FaceReading | null) => {
      if (!pose) return;
      const updated = await client.sendPose(pose, file);
      const angle = reading ? movedAngle(pose, reading) : null;
      if (angle !== null) setMeasured((known) => ({ ...known, [pose]: angle }));
      setCheck(updated);
      const rest = queue.slice(1);
      if (rest.length > 0) {
        setQueue(rest);
        return;
      }
      await finish(updated);
    },
    [client, pose, queue, finish],
  );

  const retake = check.status === "awaiting_client";
  const top = retake ? (
    <BackButton fallback="/id-check" />
  ) : (
    <StepTop step="poses" documentType={check.documentType} guided fallback="/id-check" />
  );

  if (!started || !pose) {
    return (
      <Screen signedIn footer={<Button label="Start" onPress={() => setStarted(true)} testID="poses-start" />}>
        <Stack.Screen options={{ title: "Head turns" }} />
        {top}
        <View style={{ marginTop: 28, gap: 8 }}>
          <Title>Now four quick head turns</Title>
          <Txt color={colors.muted}>
            They show us it is really you holding the phone. The camera takes each picture by itself when your face is right.
          </Txt>
        </View>
        <View style={{ marginTop: 22, gap: 14 }}>
          <Card>
            <View style={{ gap: 16 }}>
              {POSES.map((each) => {
                const done = check.poses.includes(each) && !queue.includes(each);
                return (
                  <StatusLine
                    key={each}
                    state={done ? "done" : each === queue[0] ? "now" : "next"}
                    title={poseCopy[each].short}
                    detail={done ? "Done" : poseCopy[each].title}
                  />
                );
              })}
            </View>
          </Card>
          <View style={styles.tips}>
            {["Hold your phone at eye level", "Move only your head", "Good light on your face"].map((tip) => (
              <View key={tip} style={styles.tip}>
                <Txt size={12} weight="semibold" center>
                  {tip}
                </Txt>
              </View>
            ))}
          </View>
        </View>
      </Screen>
    );
  }

  return (
    <>
      <Stack.Screen options={{ title: poseCopy[pose].title }} />
      <GuidedCapture
        key={pose}
        pose={pose}
        title={poseCopy[pose].title}
        how={poseCopy[pose].how}
        top={top}
        opposite={measured[OPPOSITE[pose]] ?? null}
        send={send}
        testID={`pose-${pose}`}
        below={<PoseDots current={pose} done={POSES.filter((each) => !queue.includes(each))} />}
      />
    </>
  );
}

const DOT_LABELS: Record<Pose, string> = { left: "Left", right: "Right", up: "Up", down: "Down" };

/** The four poses in a row: done, now, and still to come. */
function PoseDots({ current, done }: { current: Pose; done: Pose[] }) {
  return (
    <View style={styles.dots} accessibilityLabel={`${done.length} of 4 head turns done`}>
      {POSES.map((each) => {
        const finished = done.includes(each);
        const now = each === current;
        return (
          <View key={each} style={[styles.dot, finished && styles.dotDone, now && styles.dotNow]}>
            {finished ? <Icon name="check" size={12} color={colors.lime} strokeWidth={3} /> : null}
            <Txt size={fontSizes.xs} weight={now ? "bold" : "semibold"} color={finished ? colors.white : now ? colors.ink : colors.muted}>
              {DOT_LABELS[each]}
            </Txt>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  tips: { flexDirection: "row", gap: 10 },
  tip: {
    flex: 1,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 14,
    paddingVertical: 12,
    paddingHorizontal: 8,
    justifyContent: "center",
  },
  dots: { flexDirection: "row", gap: 6, justifyContent: "center" },
  dot: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  dotDone: { backgroundColor: colors.black, borderColor: colors.black },
  dotNow: { borderColor: colors.black, borderWidth: 2 },
});
