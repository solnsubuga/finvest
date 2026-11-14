import { useCallback, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Href, Stack, useFocusEffect, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { SignInAgain } from "@/components/SignInAgain";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatMoney } from "@/payments/format";
import { useProfile } from "@/profile/ProfileProvider";
import { Closure, describeProfileError, ProfileError, Standing } from "@/profile/types";
import { colors, fontSizes, radii } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; closure: Closure }
  | { status: "error"; message: string };

type Item = { key: string; title: string; detail: string; clear: boolean; href?: Href };

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** What the client sees about each thing that has to be finished first. */
function items(standing: Standing): Item[] {
  const inProgress = standing.openDeposits + standing.openPayouts;
  const owed = standing.owed.map(formatMoney).join(" and ");
  const all: (Item | null)[] = [
    standing.runningInvestments
      ? {
          key: "investments",
          title: `${plural(standing.runningInvestments, "investment", "investments")} still running`,
          detail: `${owed ? `${owed}. ` : ""}Wait for maturity, or withdraw early.`,
          clear: false,
          href: "/portfolio",
        }
      : { key: "investments", title: "No investments running", detail: "Nothing is still earning", clear: true },
    !standing.runningInvestments && standing.owed.length
      ? {
          key: "owed",
          title: "Money still to pay you",
          detail: `${owed}. Choose where we pay it, then wait for it to arrive.`,
          clear: false,
          href: "/payouts",
        }
      : null,
    standing.openGoals
      ? {
          key: "goals",
          title: `${plural(standing.openGoals, "goal", "goals")} still saving`,
          detail: "Close them so nothing more is collected",
          clear: false,
          href: "/goals",
        }
      : { key: "goals", title: "No goals saving", detail: "Nothing more will be collected", clear: true },
    standing.livePlans
      ? {
          key: "plans",
          title: `${plural(standing.livePlans, "automatic plan", "automatic plans")} on`,
          detail: "Stop them so we stop asking for payments",
          clear: false,
          href: "/autoinvest",
        }
      : null,
    inProgress
      ? {
          key: "payments",
          title: `${plural(inProgress, "payment", "payments")} in progress`,
          detail: "Wait for them to arrive. This usually takes minutes.",
          clear: false,
          href: "/activity",
        }
      : { key: "payments", title: "No payments in progress", detail: "Nothing is on its way in or out", clear: true },
  ];
  return all.filter((item): item is Item => item !== null);
}

/** Profile, Close account: what has to happen first, and the request to close. */
export default function CloseAccount() {
  const profile = useProfile();
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [signInAgain, setSignInAgain] = useState(false);

  useFocusEffect(
    useCallback(() => {
      let live = true;
      profile.getClosure().then(
        (closure) => live && setState({ status: "ready", closure }),
        (caught: unknown) => live && setState({ status: "error", message: describeProfileError(caught) }),
      );
      return () => {
        live = false;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [profile, attempt]),
  );

  const act = async (work: () => Promise<Closure>) => {
    setBusy(true);
    setNote(null);
    try {
      setState({ status: "ready", closure: await work() });
      setConfirming(false);
    } catch (caught) {
      if (caught instanceof ProfileError && caught.code === "sign_in_again") {
        setSignInAgain(true);
      } else {
        setNote(describeProfileError(caught));
        if (caught instanceof ProfileError && caught.code === "blocked") setAttempt((n) => n + 1);
      }
    } finally {
      setBusy(false);
    }
  };

  const closure = state.status === "ready" ? state.closure : null;
  const request = closure?.request ?? null;
  const open = request && (request.status === "requested" || request.status === "prepared");
  const blocked = Boolean(closure?.standing.blockers.length);

  if (signInAgain) {
    return (
      <Screen signedIn>
        <BackButton fallback="/profile" />
        <SignInAgain returnTo="/account/close" />
      </Screen>
    );
  }

  return (
    <Screen
      signedIn
      footer={
        closure && !open ? (
          <View style={{ gap: 10 }}>
            {blocked ? (
              <Button
                label="Withdraw my investments first"
                onPress={() => router.navigate("/portfolio")}
                testID="close-withdraw-first"
              />
            ) : null}
            {!confirming ? (
              <Button
                label="Close my account"
                variant="secondary"
                disabled={blocked}
                onPress={() => setConfirming(true)}
                testID="close-start"
              />
            ) : null}
          </View>
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Close account" }} />
      <BackButton fallback="/profile" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Profile</Eyebrow>
        <Title>{open ? "We have your request" : "Before you close your account"}</Title>
        <Txt color={colors.muted}>
          {open
            ? "Two people at Finvest check every request to close an account. We will be in touch within two working days."
            : "We are sorry to see you go. Here is what has to happen first."}
        </Txt>
      </View>

      {state.status === "loading" ? (
        <View style={{ marginTop: 40 }}>
          <ActivityIndicator accessibilityLabel="Loading" color={colors.black} />
        </View>
      ) : null}

      {state.status === "error" ? (
        <View style={{ marginTop: 22, gap: 12 }}>
          <Notice tone="danger">{state.message}</Notice>
          <Button label="Try again" onPress={() => setAttempt((n) => n + 1)} height={52} />
        </View>
      ) : null}

      {closure ? (
        <View style={{ marginTop: 22, gap: 14 }}>
          {note ? <Notice tone="danger">{note}</Notice> : null}
          {request?.status === "declined" && request.declineNote ? (
            <Notice tone="warning">{`We could not close your account last time: ${request.declineNote}`}</Notice>
          ) : null}
          {request?.status === "cancelled" ? (
            <Notice tone="neutral">You cancelled your request. Your account stays open.</Notice>
          ) : null}

          {open ? (
            <Card>
              <View style={{ gap: 10 }} testID="close-requested">
                <View style={[styles.badge, styles.todo]}>
                  <Txt size={12} weight="bold" color="#8A4B00">
                    {request?.status === "prepared" ? "Checked once, waiting for a second check" : "Waiting for a check"}
                  </Txt>
                </View>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  {`Asked on ${new Date(request?.requestedAt ?? "").toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}.`}
                  {request?.reason ? ` Your reason: ${request.reason}` : ""}
                </Txt>
                <Txt size={fontSizes.sm}>
                  While we check, you cannot start a new investment, goal, automatic plan or payment. Everything
                  you already have carries on. Once it is closed you will not be able to log in again.
                </Txt>
                <Button
                  label="Keep my account open"
                  variant="secondary"
                  height={52}
                  loading={busy}
                  onPress={() => request && act(() => profile.cancelClosure(request.id))}
                  testID="close-cancel"
                />
              </View>
            </Card>
          ) : (
            <Card padded={false}>
              {items(closure.standing).map((item, index) => (
                <View key={item.key} style={[styles.row, index > 0 && styles.divider]} testID={`close-item-${item.key}`}>
                  <View style={{ flex: 1 }}>
                    <Txt weight="bold">{item.title}</Txt>
                    <Txt size={fontSizes.sm} color={colors.muted}>
                      {item.detail}
                    </Txt>
                    {!item.clear && item.href ? (
                      <View style={{ marginTop: 6, alignSelf: "flex-start" }}>
                        <TextLink label="Go there" onPress={() => item.href && router.push(item.href)} />
                      </View>
                    ) : null}
                  </View>
                  <View style={[styles.badge, item.clear ? styles.clear : styles.todo]}>
                    <Txt size={12} weight="bold" color={item.clear ? "#2A3800" : "#8A4B00"}>
                      {item.clear ? "Clear" : "To do"}
                    </Txt>
                  </View>
                </View>
              ))}
            </Card>
          )}

          {confirming && !open ? (
            <Card>
              <View style={{ gap: 12 }} testID="close-confirm">
                <Txt weight="bold">Close your account for good?</Txt>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  Two people at Finvest check the request. Once it is closed you cannot log in again, and your
                  mobile number cannot open a new account. Ask us in Help if you want to come back.
                </Txt>
                <TextField
                  label="Why are you leaving? (optional)"
                  value={reason}
                  onChangeText={setReason}
                  maxLength={500}
                  testID="close-reason"
                />
                <Button
                  label="Yes, close my account"
                  height={52}
                  loading={busy}
                  onPress={() => act(() => profile.askToClose(reason.trim() || null))}
                  testID="close-confirm-button"
                />
                <TextLink label="Keep my account" onPress={() => setConfirming(false)} />
              </View>
            </Card>
          ) : null}

          <View style={styles.info}>
            <Txt size={fontSizes.sm}>
              After closing we keep your records for the period the law requires, then delete them. Your account
              number is never given to anyone else.
            </Txt>
          </View>
          {!open ? (
            <View style={[styles.info, { backgroundColor: colors.lime }]}>
              <Txt size={fontSizes.sm} color={colors.ink}>
                Just taking a break? You do not need to close anything. Leave your account as it is and log in
                whenever you are ready.
              </Txt>
            </View>
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 14, paddingHorizontal: 16 },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
  badge: { alignSelf: "flex-start", borderRadius: radii.pill, paddingVertical: 4, paddingHorizontal: 10 },
  todo: { backgroundColor: "#FDEFD8" },
  clear: { backgroundColor: "#E9F7B5" },
  info: { backgroundColor: colors.surfaceSunken, borderRadius: radii.md, paddingVertical: 12, paddingHorizontal: 14 },
});
