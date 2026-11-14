import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Linking, View } from "react-native";

import { useAuth } from "@/auth/AuthProvider";
import { describeAuthError, SelfieAttempt } from "@/auth/types";
import { colors, fontSizes } from "@/theme";

import { Card, CheckRow, Notice } from "./Blocks";
import { Button, TextLink } from "./Button";
import { Icon } from "./Icon";
import { Txt } from "./Txt";

/** How often a waiting selfie is read while the client is on the page. Tests shorten it. */
export const selfiePolling = { ms: 3000 };

const WAITING = new Set(["starting", "open", "matching"]);

/** What went wrong, as a sentence the client can act on. Never a code. */
export function describeSelfieEnd(attempt: SelfieAttempt): string {
  if (attempt.status === "expired") return "That took too long, so we stopped for your safety. Try again.";
  if (attempt.status === "not_started") return "The live selfie did not start. Try again.";
  if (attempt.failureCode === "face_not_matched") {
    return "Your face did not match the selfie on your ID check. Try again in good light, facing the camera.";
  }
  if (attempt.failureCode === "not_live") {
    return "We could not see a live person. Hold the phone at eye level and follow what the page asks.";
  }
  return "We could not finish the live selfie. Try again.";
}

type Props = {
  /** Called once a live selfie passed on this session. The change is then sent again. */
  onConfirmed: () => void;
  onCancel: () => void;
  /** What the client can do instead, such as asking support. */
  otherwise?: string;
};

/**
 * Take a live selfie: after the passkey or app, a password or number change also needs a live
 * selfie that matches the one on the client's ID check (docs/sign-in.md). It runs on our identity
 * partner's camera page, and this card moves on by itself when the partner answers.
 */
export function ConfirmWithSelfie({ onConfirmed, onCancel, otherwise }: Props) {
  const { client } = useAuth();
  const [attempt, setAttempt] = useState<SelfieAttempt | null>(null);
  const [attemptsLeft, setAttemptsLeft] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState(false);
  const [failedReads, setFailedReads] = useState(0);
  const done = useRef(false);

  const finish = useCallback(() => {
    if (done.current) return;
    done.current = true;
    onConfirmed();
  }, [onConfirmed]);

  // On arrival: a selfie may already count, or one may still be waiting from a moment ago.
  useEffect(() => {
    let current = true;
    client.getSelfieConfirmation().then(
      (state) => {
        if (!current) return;
        if (state.confirmed || !state.needed) {
          finish();
          return;
        }
        setAttemptsLeft(state.attemptsLeft);
        if (state.latest && WAITING.has(state.latest.status)) setAttempt(state.latest);
      },
      (caught: unknown) => {
        if (current) setError(describeAuthError(caught));
      },
    );
    return () => {
      current = false;
    };
  }, [client, finish]);

  const waiting = attempt !== null && WAITING.has(attempt.status);

  // Read again every few seconds while it waits, and move on when it passes.
  useEffect(() => {
    if (!attempt) return;
    if (attempt.status === "passed") {
      finish();
      return;
    }
    if (!waiting) return;
    let current = true;
    const timer = setTimeout(() => {
      client.getSelfieAttempt(attempt.id).then(
        (next) => {
          if (!current) return;
          setError(null);
          setAttempt(next);
        },
        (caught: unknown) => {
          if (!current) return;
          setError(describeAuthError(caught));
          setFailedReads((n) => n + 1);
        },
      );
    }, selfiePolling.ms);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [client, attempt, waiting, finish, failedReads]);

  const start = async () => {
    setBusy(true);
    setError(null);
    setOpened(false);
    try {
      setAttempt(await client.startSelfieConfirmation());
      setAttemptsLeft((left) => (left === null ? null : Math.max(0, left - 1)));
    } catch (caught) {
      setError(describeAuthError(caught));
    } finally {
      setBusy(false);
    }
  };

  const open = async (url: string) => {
    setError(null);
    // The server only hands out the partner's https pages. This is a second lock.
    if (!url.startsWith("https://")) {
      setError("We could not open the camera page. Try again.");
      return;
    }
    try {
      await Linking.openURL(url);
      setOpened(true);
    } catch {
      setError("We could not open the camera page. Try again.");
    }
  };

  const ended = attempt !== null && !waiting && attempt.status !== "passed";
  const usedUp = attemptsLeft === 0;

  let body;
  if (attempt?.status === "starting") {
    body = (
      <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }} testID="selfie-starting">
        <ActivityIndicator accessibilityLabel="Getting the camera page ready" color={colors.black} />
        <Txt size={fontSizes.sm} color={colors.muted}>
          Getting the camera page ready. This takes a few seconds.
        </Txt>
      </View>
    );
  } else if (attempt?.status === "open" && attempt.url) {
    const url = attempt.url;
    body = (
      <>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {opened
            ? "Follow what the page asks, then come back. This moves on by itself when it is done."
            : "It opens on a page from ioTec, our identity partner. Follow what it asks, then come back here."}
        </Txt>
        <Button
          label={opened ? "Open the camera page again" : "Open the camera page"}
          variant={opened ? "secondary" : "primary"}
          height={52}
          onPress={() => void open(url)}
          testID="selfie-open"
        />
        {opened ? (
          <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }} testID="selfie-waiting">
            <ActivityIndicator accessibilityLabel="Waiting for the result" color={colors.black} />
            <Txt size={fontSizes.sm} color={colors.muted}>
              Waiting for the result
            </Txt>
          </View>
        ) : null}
      </>
    );
  } else if (attempt?.status === "matching") {
    body = (
      <View style={{ gap: 10 }} testID="selfie-matching">
        <CheckRow met>A live person, not a photo</CheckRow>
        <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }}>
          <ActivityIndicator accessibilityLabel="Matching your face" color={colors.black} />
          <Txt size={fontSizes.sm} color={colors.muted}>
            Matching your face to the selfie on your ID check
          </Txt>
        </View>
      </View>
    );
  } else {
    body = (
      <>
        <Txt size={fontSizes.sm} color={colors.muted}>
          For your safety, a password or number change also needs a live selfie. We match it to the selfie on your
          ID check, so nobody else can take over your account with your phone.
        </Txt>
        {ended ? (
          <View testID="selfie-ended">
            <Notice tone="danger">{describeSelfieEnd(attempt)}</Notice>
          </View>
        ) : null}
        {usedUp ? (
          <View testID="selfie-used-up">
            <Notice tone="warning">
              {`You have taken 5 live selfies today. Try again tomorrow.${otherwise ? ` ${otherwise}` : ""}`}
            </Notice>
          </View>
        ) : (
          <Button
            label={ended ? "Try again" : "Take a live selfie"}
            height={52}
            loading={busy}
            onPress={() => void start()}
            testID="selfie-start"
          />
        )}
        {attemptsLeft !== null && !usedUp && ended ? (
          <Txt size={fontSizes.sm} color={colors.muted}>
            {`${attemptsLeft} ${attemptsLeft === 1 ? "try" : "tries"} left today.`}
          </Txt>
        ) : null}
      </>
    );
  }

  return (
    <Card>
      <View style={{ gap: 12 }} testID="confirm-with-selfie">
        <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }}>
          <Icon name="profile" size={22} />
          <Txt weight="bold">Take a live selfie</Txt>
        </View>
        <CheckRow met>Passkey or app confirmed</CheckRow>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {body}
        <View style={{ alignSelf: "center" }}>
          <TextLink label="Not now" onPress={onCancel} testID="selfie-cancel" />
        </View>
      </View>
    </Card>
  );
}
