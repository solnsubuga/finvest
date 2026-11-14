import { useState } from "react";
import { Pressable, Share, StyleSheet, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { Redirect, Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { useIdentity } from "@/identity/IdentityProvider";
import { Badge, Progress, StatusLine } from "@/identity/Parts";
import { hrefFor, nextStep, stepsFor } from "@/identity/steps";
import {
  beingChecked,
  describeIdentityError,
  documentCopy,
  formatAccountNumber,
  IdentityCheck,
  IdentityError,
  PhotoKind,
  photoName,
} from "@/identity/types";
import { useIdentityCheck } from "@/identity/useIdentityCheck";
import { colors, fontSizes } from "@/theme";

const HOME = "/";

function firstName(check: IdentityCheck): string | null {
  return check.details?.givenNames.split(/\s+/)[0] ?? null;
}

/** What `missing` names, in words, for a check that could not be handed in. */
function missingWords(check: IdentityCheck, missing: string[]): string {
  const names = missing.map((item) =>
    item === "details" ? "your details" : photoName(item as PhotoKind, check.documentType).toLowerCase(),
  );
  return `Still needed: ${names.join(", ")}.`;
}

/** A draft: the steps, which are done, and the way on. */
function Draft({ check, onSent }: { check: IdentityCheck; onSent: (check: IdentityCheck) => void }) {
  const router = useRouter();
  const { client } = useIdentity();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const next = nextStep(check);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      onSent(await client.submit());
    } catch (caught) {
      setError(
        caught instanceof IdentityError && caught.code === "incomplete" && caught.missing.length > 0
          ? missingWords(check, caught.missing)
          : describeIdentityError(caught),
      );
    } finally {
      setBusy(false);
    }
  };

  const titles: Record<string, string> = {
    document: documentCopy[check.documentType].name,
    front: check.documentType === "passport" ? "Photo page" : "Front of your ID",
    back: "Back of your ID",
    details: "Your details",
    selfie: "Selfie",
  };
  const steps = stepsFor(check.documentType);
  const firstOpen = steps.find((step) => step !== "document" && check.missing.includes(step));

  return (
    <Screen
      signedIn
      footer={
        next ? (
          <Button label="Carry on" onPress={() => router.push(hrefFor(next))} testID="id-check-continue" />
        ) : (
          <Button label="Send for checking" onPress={send} loading={busy} testID="id-check-send" />
        )
      }
    >
      <Stack.Screen options={{ title: "Your ID check" }} />
      <BackButton fallback={HOME} />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>{next ? "Finish your ID check" : "Ready to send"}</Title>
        <Txt color={colors.muted}>
          {next
            ? "Pick up where you left off. What you have done is saved."
            : "Everything is in. Send it and we will check it."}
        </Txt>
      </View>
      <View style={{ marginTop: 22, gap: 14 }}>
        <Card>
          <View style={{ gap: 16 }}>
            {steps.map((step) => {
              const done = step === "document" || !check.missing.includes(step);
              const state: Progress = done ? "done" : step === firstOpen ? "now" : "next";
              return (
                <Pressable
                  key={step}
                  accessibilityRole="link"
                  onPress={() => router.push(hrefFor(step))}
                  style={({ pressed }) => pressed && { opacity: 0.7 }}
                  testID={`id-step-${step}`}
                >
                  <StatusLine state={state} title={titles[step] ?? step} detail={done ? "Done. Tap to change it." : "Not done yet"} />
                </Pressable>
              );
            })}
          </View>
        </Card>
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </View>
    </Screen>
  );
}

/** Handed in and with us. Which of our steps it is at stays with us. */
function Checking({ check }: { check: IdentityCheck }) {
  const router = useRouter();
  const name = firstName(check);
  return (
    <Screen signedIn footer={<Button label="Go to my account" variant="lime" onPress={() => router.replace(HOME)} testID="id-check-home" />}>
      <Stack.Screen options={{ title: "We are checking your ID" }} />
      <View style={{ marginTop: 24 }}>
        <Badge icon="check" />
      </View>
      <View style={{ marginTop: 22, gap: 10 }}>
        <Title>{name ? `Thanks, ${name}. We are checking now.` : "Thanks. We are checking now."}</Title>
        <Txt color={colors.muted}>You do not need to wait on this screen. We will send you a notification when it is decided, or if we need a new photo.</Txt>
      </View>
      <View style={{ marginTop: 22, gap: 14 }}>
        <Card>
          <View style={{ gap: 16 }}>
            <StatusLine
              state="done"
              title="ID photos received"
              detail={check.documentType === "passport" ? "Your photo page and selfie" : "Front, back and selfie"}
            />
            <StatusLine state="done" title="Details received" detail="As printed on your ID" />
            <StatusLine state="now" title="Confirming who you are" detail="A person at Finvest checks every new account" />
            <StatusLine state="next" title="Account fully open" detail="Your account number, investing and payouts" />
          </View>
        </Card>
        <View style={styles.lime}>
          <Txt weight="extrabold" color={colors.limeText}>
            You can look around already
          </Txt>
          <Txt size={fontSizes.sm} color={colors.limeText}>
            Plan your first investment or set up a goal. Paying in opens the moment your check passes.
          </Txt>
        </View>
        <Txt size={fontSizes.sm} color={colors.muted}>
          If a photo is unclear, we tell you which one to take again. You will not need to start over.
        </Txt>
      </View>
    </Screen>
  );
}

/** Staff asked for named photos again. Only those are taken, then sent. */
function Retake({ check, onSent }: { check: IdentityCheck; onSent: (check: IdentityCheck) => void }) {
  const router = useRouter();
  const { client, retaken } = useIdentity();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const done = retaken(check.id);
  const allDone = check.retakePhotos.every((kind) => done.includes(kind));
  const several = check.retakePhotos.length > 1;

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      onSent(await client.submit());
    } catch (caught) {
      setError(
        caught instanceof IdentityError && caught.code === "incomplete"
          ? `Take ${several ? "each photo" : "the photo"} again first, then send ${several ? "them" : "it"}.`
          : describeIdentityError(caught),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        <Button
          label={several ? "Send the new photos" : "Send the new photo"}
          onPress={send}
          loading={busy}
          disabled={!allDone}
          testID="retake-send"
        />
      }
    >
      <Stack.Screen options={{ title: "We need a new photo" }} />
      <BackButton fallback={HOME} />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>{several ? "We need new photos" : "We need a new photo"}</Title>
        <Txt color={colors.muted}>{`Everything else you sent is fine, so you only take ${several ? "these" : "this one"} again.`}</Txt>
      </View>
      <View style={{ marginTop: 22, gap: 14 }}>
        {check.clientMessage ? (
          <Card>
            <View style={{ gap: 6 }}>
              <Txt size={fontSizes.xs} weight="bold" color={colors.muted}>
                FROM FINVEST
              </Txt>
              <Txt testID="retake-message">{check.clientMessage}</Txt>
            </View>
          </Card>
        ) : null}
        <Card padded={false}>
          {check.retakePhotos.map((kind, index) => {
            const taken = done.includes(kind);
            return (
              <Pressable
                key={kind}
                accessibilityRole="button"
                accessibilityLabel={`${taken ? "Taken again" : "Take again"}: ${photoName(kind, check.documentType)}`}
                onPress={() => router.push(hrefFor(kind))}
                style={({ pressed }) => [styles.retakeRow, index > 0 && styles.divider, pressed && { opacity: 0.7 }]}
                testID={`retake-${kind}`}
              >
                <View style={{ flex: 1 }}>
                  <StatusLine
                    state={taken ? "done" : "now"}
                    title={photoName(kind, check.documentType)}
                    detail={taken ? "Taken again. Tap to change it." : "Tap to take it again"}
                  />
                </View>
                <Icon name="chevron" size={16} strokeWidth={2.2} />
              </Pressable>
            );
          })}
        </Card>
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </View>
    </Screen>
  );
}

/** Approved: the account number, and what it is for. */
function Approved({ check }: { check: IdentityCheck }) {
  const router = useRouter();
  const [copied, setCopied] = useState(false);
  const number = check.accountNumber ?? "";
  const name = firstName(check);
  const holder = check.details ? `${check.details.givenNames} ${check.details.surname.charAt(0)}${check.details.surname.slice(1).toLowerCase()}` : null;

  const copy = async () => {
    await Clipboard.setStringAsync(number);
    setCopied(true);
    setTimeout(() => setCopied(false), 2_000);
  };
  const share = () => {
    void Share.share({ message: `My Finvest account number is ${formatAccountNumber(number)}` });
  };

  const uses = [
    { title: "Bank transfers", detail: "Put it as the reference so your money is matched on its own" },
    { title: "Statements and certificates", detail: "It appears on every document we send you" },
    { title: "Getting help", detail: "Quote it when you contact us" },
  ];

  return (
    <Screen
      signedIn
      footer={<Button label="Make my first investment" variant="lime" onPress={() => router.replace("/invest")} testID="id-check-invest" />}
    >
      <Stack.Screen options={{ title: "You are verified" }} />
      <BackButton fallback={HOME} />
      <View style={{ alignItems: "center", marginTop: 12, gap: 8 }}>
        <Badge icon="check" size={88} />
        <View style={{ marginTop: 14 }}>
          <Title>{name ? `You are verified, ${name}` : "You are verified"}</Title>
        </View>
        <Txt center color={colors.muted}>
          This is your Finvest account number. It is yours for life.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>
        <View style={styles.numberCard}>
          <Txt size={12} weight="bold" color={colors.onBlackMuted} style={{ letterSpacing: 0.8 }}>
            FINVEST ACCOUNT NUMBER
          </Txt>
          <Txt size={32} weight="extrabold" color={colors.white} testID="account-number" style={{ letterSpacing: 1, fontVariant: ["tabular-nums"] }}>
            {formatAccountNumber(number)}
          </Txt>
          {holder ? (
            <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
              {`Personal account, ${holder}`}
            </Txt>
          ) : null}
          <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
            <Pressable accessibilityRole="button" accessibilityLabel="Copy account number" onPress={copy} style={[styles.pill, { backgroundColor: colors.lime }]}>
              <Txt size={13} weight="bold" accessibilityLiveRegion="polite">
                {copied ? "Copied" : "Copy"}
              </Txt>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Share account number" onPress={share} style={[styles.pill, styles.pillOutline]}>
              <Txt size={13} weight="bold" color={colors.white}>
                Share
              </Txt>
            </Pressable>
          </View>
        </View>
        <Card padded={false}>
          {uses.map((use, index) => (
            <View key={use.title} style={[styles.useRow, index > 0 && styles.divider]}>
              <Txt weight="bold">{use.title}</Txt>
              <Txt size={fontSizes.sm} color={colors.muted}>
                {use.detail}
              </Txt>
            </View>
          ))}
        </Card>
      </View>
    </Screen>
  );
}

/** Rejected. Final, so the way on is to talk to us. */
function Rejected({ check }: { check: IdentityCheck }) {
  const router = useRouter();
  return (
    <Screen signedIn footer={<Button label="Write to us in Help" onPress={() => router.push("/help")} testID="id-check-help" />}>
      <Stack.Screen options={{ title: "We could not confirm your identity" }} />
      <BackButton fallback={HOME} />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>We could not confirm your identity</Title>
        <Txt color={colors.muted}>
          So we cannot open an investment account for you. Nothing was taken from you, and you can still sign in.
        </Txt>
      </View>
      <View style={{ marginTop: 22, gap: 14 }}>
        {check.clientMessage ? (
          <Card>
            <View style={{ gap: 6 }}>
              <Txt size={fontSizes.xs} weight="bold" color={colors.muted}>
                FROM FINVEST
              </Txt>
              <Txt testID="rejected-message">{check.clientMessage}</Txt>
            </View>
          </Card>
        ) : null}
        <Txt size={fontSizes.sm} color={colors.muted}>
          If you think this is a mistake, write to us and a person will look at it again with you.
        </Txt>
      </View>
    </Screen>
  );
}

/** The ID check: where it stands, and the next thing to do. Home and other screens link here. */
export default function IdCheck() {
  const state = useIdentityCheck();

  if (state.status !== "ready") {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Your ID check" }} />
        <BackButton fallback={HOME} />
        <View style={{ marginTop: 28, gap: 16 }}>
          <Title>Your ID check</Title>
          {state.status === "loading" ? (
            <Loading label="Loading your ID check" />
          ) : (
            <LoadFailed message={state.message} onRetry={state.reload} />
          )}
        </View>
      </Screen>
    );
  }

  const check = state.check;
  if (!check) return <Redirect href="/id-check/document" />;
  if (check.status === "draft") return <Draft check={check} onSent={state.replace} />;
  if (check.status === "awaiting_client") return <Retake check={check} onSent={state.replace} />;
  if (check.status === "approved") return <Approved check={check} />;
  if (check.status === "rejected") return <Rejected check={check} />;
  if (beingChecked(check.status)) return <Checking check={check} />;
  return null;
}

const styles = StyleSheet.create({
  lime: { backgroundColor: colors.limeSurface, borderRadius: 20, paddingVertical: 16, paddingHorizontal: 18, gap: 4 },
  retakeRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 14, paddingHorizontal: 18 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  numberCard: { backgroundColor: colors.black, borderRadius: 24, paddingVertical: 22, paddingHorizontal: 20, alignItems: "center", gap: 6 },
  pill: { height: 44, paddingHorizontal: 18, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  pillOutline: { borderWidth: 1, borderColor: colors.onBlackBorder },
  useRow: { paddingVertical: 12, paddingHorizontal: 18, gap: 2 },
});
