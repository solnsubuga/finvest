import { ReactNode, useCallback, useRef, useState } from "react";
import { ActivityIndicator, Image, StyleSheet, View } from "react-native";

import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

import { GuideCamera } from "./camera/GuideCamera";
import type { CameraTrouble, GuideCameraHandle } from "./camera/types";
import { FaceReading, guide, Steadiness } from "./guidance";
import { checkPhoto, photoProblemCopy } from "./photo";
import { takePhoto } from "./pickPhoto";
import { FrameProblem, LiveProblem, liveCopy, Pose, retakeCopy, turns } from "./poses";
import { describeIdentityError, IdentityError, PhotoFile } from "./types";

type Props = {
  /** The pose to take, or null for the straight-on selfie. */
  pose: Pose | null;
  title: string;
  how: string;
  /** The back button or step header. */
  top: ReactNode;
  /** The angle the phone measured for the opposite pose's frame in this visit, if any. */
  opposite: number | null;
  /** Shown under the camera, such as which poses are done. */
  below?: ReactNode;
  /** Shown above the camera, such as staff's message during a retake. */
  above?: ReactNode;
  /** Sends the picture. Throws an IdentityError when it is refused or cannot be sent. */
  send: (file: PhotoFile, reading: FaceReading | null) => Promise<void>;
  /** The test ID prefix, such as "pose-left" or "selfie". */
  testID: string;
};

type Phase =
  | { kind: "live" }
  | { kind: "sending"; file: PhotoFile }
  /** It could not be checked just now (offline or 503). The same picture can be sent again. */
  | { kind: "failed"; file: PhotoFile; message: string };

/**
 * The live camera for the guided selfie: an oval to fit the face in, a prompt that says what to
 * change, and the picture taken on its own once the face has been right for a moment. The API
 * checks the picture again, and a refusal comes back here as a prompt to take it again.
 *
 * Give it a `key` per pose, so each starts afresh.
 *
 * When the device cannot find faces, the client presses the button to take it. When there is no
 * live camera at all, the phone's own camera app takes it.
 */
export function GuidedCapture({ pose, title, how, top, opposite, below, above, send, testID }: Props) {
  const camera = useRef<GuideCameraHandle | null>(null);
  const steady = useRef(new Steadiness());
  const taking = useRef(false);
  const lastReading = useRef<FaceReading | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "live" });
  const [live, setLive] = useState<LiveProblem | null | undefined>(undefined);
  const [refused, setRefused] = useState<FrameProblem[]>([]);
  const [trouble, setTrouble] = useState<CameraTrouble | null>(null);
  const [error, setError] = useState<string | null>(null);

  const deliver = useCallback(
    async (file: PhotoFile, reading: FaceReading | null) => {
      setPhase({ kind: "sending", file });
      setError(null);
      try {
        await send(file, reading);
        setRefused([]);
      } catch (caught) {
        if (caught instanceof IdentityError && caught.code === "retake") {
          setRefused(caught.problems);
          setPhase({ kind: "live" });
        } else if (caught instanceof IdentityError && (caught.code === "unavailable" || caught.code === "offline")) {
          setPhase({ kind: "failed", file, message: describeIdentityError(caught) });
        } else {
          setError(describeIdentityError(caught));
          setPhase({ kind: "live" });
        }
      } finally {
        steady.current.reset();
        taking.current = false;
      }
    },
    [send],
  );

  const take = useCallback(async () => {
    if (taking.current) return;
    taking.current = true;
    const reading = lastReading.current;
    try {
      const picked = await camera.current?.capture();
      const checked = picked ? checkPhoto(picked) : null;
      if (!checked || typeof checked === "string") {
        if (typeof checked === "string") setError(photoProblemCopy[checked]);
        taking.current = false;
        return;
      }
      await deliver(checked, reading);
    } catch {
      taking.current = false;
      setError("The camera could not take the picture. Try again.");
    }
  }, [deliver]);

  const onReading = useCallback(
    (reading: FaceReading) => {
      if (taking.current) return;
      lastReading.current = reading;
      const problem = guide(pose, reading, opposite);
      setLive(problem);
      if (steady.current.feed(problem, Date.now())) void take();
    },
    [pose, opposite, take],
  );

  const onTrouble = useCallback((next: CameraTrouble) => setTrouble(next), []);

  /** No live camera: the phone's own camera app takes the picture, and the API checks it. */
  const openCameraApp = async () => {
    setError(null);
    const result = await takePhoto(true);
    if (result === null) return;
    if (result === "no_permission") {
      setError("Finvest needs your camera for this. Allow it in your phone's settings, then try again.");
      return;
    }
    const checked = checkPhoto(result);
    if (typeof checked === "string") {
      setError(photoProblemCopy[checked]);
      return;
    }
    await deliver(checked, null);
  };

  const noCamera = trouble === "no_permission" || trouble === "no_camera";
  const sending = phase.kind === "sending";
  const good = live === null;

  const footer =
    phase.kind === "failed" ? (
      <>
        <Button label="Try again" onPress={() => void deliver(phase.file, lastReading.current)} testID={`${testID}-retry`} />
        <TextLink label="Take it again" onPress={() => setPhase({ kind: "live" })} />
      </>
    ) : noCamera ? (
      <Button label="Use my camera app" onPress={() => void openCameraApp()} loading={sending} testID={`${testID}-camera-app`} />
    ) : trouble === "no_detector" ? (
      <Button label="Take the picture" onPress={() => void take()} loading={sending} testID={`${testID}-take`} />
    ) : null;

  return (
    <Screen signedIn footer={footer}>
      {top}
      <View style={{ marginTop: 24, gap: 8 }}>
        <Title>{title}</Title>
        <Txt color={colors.muted}>{how}</Txt>
      </View>
      <View style={{ marginTop: 18, gap: 14 }}>
        {above}
        {refused.length > 0 ? (
          <View testID={`${testID}-refused`}>
            <Notice tone="warning">{refused.map((problem) => retakeCopy(problem, pose)).join(" ")}</Notice>
          </View>
        ) : null}
        <View style={styles.box} testID={`${testID}-camera`}>
          {noCamera ? (
            <View style={styles.blocked}>
              <Icon name="eyeOff" size={28} color={colors.white} />
              <Txt center color={colors.white} size={fontSizes.sm}>
                {trouble === "no_permission"
                  ? "Finvest cannot use your camera. Allow it in your settings, or use your camera app."
                  : "Your camera would not start here. Use your camera app instead."}
              </Txt>
            </View>
          ) : (
            <GuideCamera
              ref={camera}
              active={phase.kind === "live"}
              fileName={pose ? `pose-${pose}.jpg` : "selfie.jpg"}
              onReading={onReading}
              onTrouble={onTrouble}
              style={StyleSheet.absoluteFill}
            />
          )}
          {phase.kind !== "live" ? (
            <Image source={{ uri: phase.file.uri }} style={[StyleSheet.absoluteFill, styles.mirror]} resizeMode="cover" />
          ) : null}
          {!noCamera ? (
            <View style={styles.overlay} pointerEvents="none">
              <View style={[styles.oval, good && phase.kind === "live" && styles.ovalGood]} />
              {pose && phase.kind === "live" ? <PoseArrow pose={pose} /> : null}
            </View>
          ) : null}
          {!noCamera ? (
            <View style={styles.promptRow} pointerEvents="none">
              <View style={[styles.prompt, good && phase.kind === "live" && styles.promptGood]} accessibilityLiveRegion="polite">
                {sending ? <ActivityIndicator size="small" color={colors.white} /> : null}
                <Txt size={fontSizes.sm} weight="bold" color={good && phase.kind === "live" ? colors.limeText : colors.white} testID={`${testID}-prompt`}>
                  {phase.kind === "sending"
                    ? "Checking the picture"
                    : phase.kind === "failed"
                      ? "Not checked yet"
                      : trouble === "no_detector"
                        ? "Fit your face in the oval, then take it"
                        : live === undefined
                          ? "Starting the camera"
                          : liveCopy(live, pose)}
                </Txt>
              </View>
            </View>
          ) : null}
        </View>
        {below}
        {phase.kind === "failed" ? <Notice tone="danger">{`We could not check that picture just now. ${phase.message}`}</Notice> : null}
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <Txt size={fontSizes.xs} color={colors.muted}>
          Your face is found on this device. Only the pictures you take are sent, to match you to your ID.
        </Txt>
      </View>
    </Screen>
  );
}

/** Which way to move, at the oval's edge: the screen is a mirror, so "your left" is on the left. */
function PoseArrow({ pose }: { pose: Pose }) {
  const place =
    pose === "left"
      ? { left: 10, top: "45%" as const }
      : pose === "right"
        ? { right: 10, top: "45%" as const }
        : pose === "up"
          ? { top: 12, alignSelf: "center" as const }
          : { bottom: 64, alignSelf: "center" as const };
  const rotate = pose === "left" ? "180deg" : pose === "right" ? "0deg" : pose === "up" ? "-90deg" : "90deg";
  return (
    <View style={[styles.arrow, place, turns(pose) ? null : { left: "50%", marginLeft: -22 }]}>
      <View style={{ transform: [{ rotate }] }}>
        <Icon name="chevron" size={22} color={colors.black} strokeWidth={3} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    width: "100%",
    maxWidth: 380,
    alignSelf: "center",
    aspectRatio: 3 / 4,
    borderRadius: 22,
    overflow: "hidden",
    backgroundColor: colors.ink,
  },
  mirror: { transform: [{ scaleX: -1 }] },
  overlay: { position: "absolute", top: 0, right: 0, bottom: 0, left: 0, alignItems: "center", justifyContent: "center" },
  oval: {
    width: "64%",
    aspectRatio: 0.78,
    borderRadius: 999,
    borderWidth: 3,
    borderColor: "rgba(255,255,255,0.85)",
    borderStyle: "dashed",
  },
  ovalGood: { borderColor: colors.lime, borderStyle: "solid", borderWidth: 4 },
  arrow: {
    position: "absolute",
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
  },
  promptRow: { position: "absolute", left: 0, right: 0, bottom: 14, alignItems: "center" },
  prompt: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 999,
    backgroundColor: "rgba(13,13,13,0.78)",
  },
  promptGood: { backgroundColor: colors.lime },
  blocked: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, padding: 28 },
});
