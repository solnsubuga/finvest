import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Linking, StyleSheet, View } from "react-native";

import { Card, CheckRow, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

import { useIdentity } from "./IdentityProvider";
import { describeIdentityError, IdentityCheck, IdentityError, LivenessState } from "./types";

/** How often an open live check is read while the client is on the page. */
export const LIVENESS_POLL_MS = 3000;

type Props = {
  check: IdentityCheck;
  /** The back button or step header, as the other photo steps show it. */
  top: ReactNode;
  /** The face frame the camera step shows. */
  frame: ReactNode;
  /** Take the selfie with the camera instead: no live check, or the client chose it. */
  onCamera: () => void;
  /** The live capture is now the selfie. `updated` is the check read again. */
  onDone: (updated: IdentityCheck) => Promise<void>;
};

const tips = ["Good light on your face", "Look at the camera", "No hat or glasses"];

/**
 * The selfie, taken as a live check on our partner's page (docs/identity-checks.md#liveness).
 * The page proves a live person is in front of the camera, and what it captures becomes the
 * selfie. A camera photo stays possible, and staff see that it was not live.
 */
export function LiveSelfie({ check, top, frame, onCamera, onDone }: Props) {
  const { client } = useIdentity();
  const [state, setState] = useState<LivenessState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState(false);
  // Counts failed reads while a check waits, so the next read is still scheduled.
  const [failedReads, setFailedReads] = useState(0);
  const finishing = useRef(false);

  const read = useCallback(async () => {
    try {
      const next = await client.getLiveness();
      setLoadError(null);
      setState(next);
      return next;
    } catch (caught) {
      setLoadError(describeIdentityError(caught));
      return null;
    }
  }, [client]);

  const latest = state?.latest ?? null;
  const waiting = latest?.status === "starting" || latest?.status === "open";

  // Read on arrival, then again every LIVENESS_POLL_MS while a check waits.
  useEffect(() => {
    if (state && !waiting) return;
    let current = true;
    const timer = setTimeout(
      () => {
        client.getLiveness().then(
          (next) => {
            if (!current) return;
            setLoadError(null);
            setState(next);
            if (!state && !next.available) onCamera();
          },
          (caught: unknown) => {
            if (!current) return;
            setLoadError(describeIdentityError(caught));
            // With nothing read yet the page offers a retry. While a check waits, keep reading.
            if (state) setFailedReads((n) => n + 1);
          },
        );
      },
      state ? LIVENESS_POLL_MS : 0,
    );
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [client, state, waiting, onCamera, failedReads]);

  useEffect(() => {
    if (latest?.status !== "passed" || finishing.current) return;
    if (latest.photoUsed) return;
    // Passed, but the check moved on meanwhile, for example it was handed in elsewhere.
    finishing.current = true;
    void client.getCheck().then((updated) => (updated ? onDone(updated) : undefined));
  }, [latest, client, onDone]);

  const start = async () => {
    setBusy(true);
    setError(null);
    setOpened(false);
    try {
      await client.startLiveness();
      await read();
    } catch (caught) {
      if (caught instanceof IdentityError && caught.code === "liveness_used_up") await read();
      setError(describeIdentityError(caught));
    } finally {
      setBusy(false);
    }
  };

  const open = async (url: string) => {
    setError(null);
    // The server only hands out the partner's https pages. This is a second lock.
    if (!url.startsWith("https://")) {
      setError("We could not open the live check. Try again, or take a photo instead.");
      return;
    }
    try {
      await Linking.openURL(url);
      setOpened(true);
    } catch {
      setError("We could not open the live check. Try again, or take a photo instead.");
    }
  };

  const finish = async () => {
    setBusy(true);
    try {
      const updated = await client.getCheck();
      if (updated) await onDone(updated);
    } catch (caught) {
      setError(describeIdentityError(caught));
    } finally {
      setBusy(false);
    }
  };

  const retake = check.status === "awaiting_client";
  const errorNotice = error ? <Notice tone="danger">{error}</Notice> : null;

  if (!state) {
    return (
      <Screen signedIn>
        {top}
        <View style={{ marginTop: 24 }}>
          {loadError ? (
            <LoadFailed message={loadError} onRetry={() => void read()} />
          ) : (
            <Loading label="Loading your live check" />
          )}
        </View>
      </Screen>
    );
  }

  const usedUp = state.problem === "too_many_attempts";
  const cameraLink = <TextLink label="Take a photo instead" onPress={onCamera} testID="live-camera" />;

  if (latest?.status === "passed") {
    return (
      <Screen
        signedIn
        footer={
          <Button
            label={retake ? "Send my new selfie" : "Hand in my ID check"}
            onPress={() => void finish()}
            loading={busy}
            testID="live-continue"
          />
        }
      >
        {top}
        <View style={{ marginTop: 28, gap: 8 }}>
          <Title>Live selfie done</Title>
          <Txt color={colors.muted}>
            The check saw a live person. What it captured is now your selfie, and we match it to your ID.
          </Txt>
        </View>
        <View style={{ marginTop: 20, gap: 14 }}>
          <Card>
            <View style={{ gap: 10 }}>
              <CheckRow met>A live person, not a photo</CheckRow>
              <CheckRow met>Your selfie is saved</CheckRow>
            </View>
          </Card>
          {errorNotice}
        </View>
      </Screen>
    );
  }

  if (latest?.status === "starting") {
    return (
      <Screen signedIn footer={cameraLink}>
        {top}
        <View style={{ marginTop: 28, gap: 8 }}>
          <Title>Getting your live check ready</Title>
          <Txt color={colors.muted}>This takes a few seconds.</Txt>
        </View>
        <View style={styles.waiting} testID="live-starting">
          <ActivityIndicator accessibilityLabel="Getting your live check ready" color={colors.black} />
        </View>
      </Screen>
    );
  }

  if (latest?.status === "open" && latest.url) {
    const url = latest.url;
    return (
      <Screen
        signedIn
        footer={
          <>
            <Button
              label={opened ? "Open the live check again" : "Open the live check"}
              variant={opened ? "secondary" : "primary"}
              onPress={() => void open(url)}
              testID="live-open"
            />
            {opened ? (
              <Button label="I have finished" onPress={() => void read()} testID="live-finished" />
            ) : null}
            <TextLink label="Start again" onPress={() => void start()} testID="live-restart" />
          </>
        }
      >
        {top}
        <View style={{ marginTop: 28, gap: 8 }}>
          <Title>{opened ? "Finish the check, then come back" : "Your live check is ready"}</Title>
          <Txt color={colors.muted}>
            {opened
              ? "Follow what the page asks. This screen moves on by itself when the check is done."
              : "It opens on a page from ioTec, our identity partner. Follow what it asks, then come back here."}
          </Txt>
        </View>
        <View style={{ marginTop: 20, gap: 14 }}>
          {opened ? (
            <View style={styles.waiting} testID="live-waiting">
              <ActivityIndicator accessibilityLabel="Waiting for the result" color={colors.black} />
              <Txt size={fontSizes.sm} color={colors.muted}>
                Waiting for the result
              </Txt>
            </View>
          ) : (
            <Card>
              <View style={{ gap: 10 }}>
                <Txt size={fontSizes.sm} weight="bold">
                  What happens
                </Txt>
                <Step n={1}>The page asks to use your camera. Allow it.</Step>
                <Step n={2}>Look at the camera and move as it asks.</Step>
                <Step n={3}>Come back to Finvest. We take it from there.</Step>
              </View>
            </Card>
          )}
          {errorNotice}
        </View>
      </Screen>
    );
  }

  // Nothing started yet, or the last check did not pass.
  const failed = latest?.status === "failed";
  const unfinished = latest?.status === "expired" || latest?.status === "not_started";
  const heading = failed
    ? "We could not finish the live check"
    : unfinished
      ? "Your live check did not finish"
      : "Take a live selfie";
  const lead = failed
    ? "It happens in poor light or when the face is partly hidden. Try again facing a window, with your whole face in view."
    : unfinished
      ? "It ran out of time or could not start. Start a new one, it only takes a minute."
      : "A short check on a page from ioTec, our identity partner. You look at the camera and move a little, so we know a real person is in front of the camera. It takes about a minute.";

  return (
    <Screen
      signedIn
      footer={
        usedUp ? (
          <Button label="Take a selfie" onPress={onCamera} testID="live-camera" />
        ) : (
          <>
            <Button
              label={failed || unfinished ? "Try again" : "Start live check"}
              onPress={() => void start()}
              loading={busy}
              testID="live-start"
            />
            {cameraLink}
          </>
        )
      }
    >
      {top}
      <View style={{ marginTop: 28, gap: 8 }}>
        <Title>{heading}</Title>
        <Txt color={colors.muted}>{lead}</Txt>
      </View>
      <View style={{ marginTop: 22, gap: 16 }}>
        {retake && check.clientMessage ? <Notice tone="warning">{check.clientMessage}</Notice> : null}
        {usedUp ? <Notice tone="warning">{describeIdentityError(new IdentityError("liveness_used_up"))}</Notice> : null}
        {failed && !usedUp ? (
          <Notice tone="neutral">{`${state.attemptsLeft} ${state.attemptsLeft === 1 ? "try" : "tries"} left.`}</Notice>
        ) : null}
        {frame}
        <View style={styles.tips}>
          {tips.map((tip) => (
            <View key={tip} style={styles.tip}>
              <Txt size={12} weight="semibold" center>
                {tip}
              </Txt>
            </View>
          ))}
        </View>
        <Txt size={fontSizes.xs} color={colors.muted}>
          ioTec takes the picture and gets no name or number from us. Finvest uses it only to confirm it is you.
        </Txt>
        {errorNotice}
      </View>
    </Screen>
  );
}

function Step({ n, children }: { n: number; children: string }) {
  return (
    <View style={{ flexDirection: "row", gap: 10, alignItems: "flex-start" }}>
      <View style={styles.number}>
        <Txt size={12} weight="bold" color={colors.white}>
          {String(n)}
        </Txt>
      </View>
      <Txt size={14} style={{ flex: 1 }}>
        {children}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  waiting: {
    marginTop: 20,
    paddingVertical: 28,
    alignItems: "center",
    gap: 12,
    borderRadius: 18,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
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
  number: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
});
