import { useCallback, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice, StepProgress } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Choice } from "@/components/Choice";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { PROFILE_COPY, QUESTION_COPY } from "@/profile/questions";
import { useProfile } from "@/profile/ProfileProvider";
import { describeProfileError, RiskAnswers, RiskProfile } from "@/profile/types";
import { colors, fontSizes, radii } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; risk: RiskProfile }
  | { status: "error"; message: string };

function longDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

/** Profile, About your investing: four questions, so we suggest investments that suit the client. */
export default function InvestingQuestions() {
  const profile = useProfile();
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  // null shows the result; a number is the question being answered.
  const [step, setStep] = useState<number | null>(null);
  const [answers, setAnswers] = useState<RiskAnswers>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      let live = true;
      profile.getRiskProfile().then(
        (risk) => {
          if (!live) return;
          setState({ status: "ready", risk });
          setStep(risk.latest && !risk.due ? null : 0);
          setAnswers(risk.latest?.answers ?? {});
        },
        (caught: unknown) => live && setState({ status: "error", message: describeProfileError(caught) }),
      );
      return () => {
        live = false;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [profile, attempt]),
  );

  const risk = state.status === "ready" ? state.risk : null;
  const questions = risk?.questions ?? [];
  const question = step !== null ? questions[step] : undefined;
  const copy = question ? QUESTION_COPY[question.code] : undefined;

  const next = async () => {
    if (step === null || !question || !answers[question.code]) return;
    if (step < questions.length - 1) {
      setStep(step + 1);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await profile.answerRiskQuestions(answers);
      setState({ status: "ready", risk: saved });
      setStep(null);
    } catch (caught) {
      setError(describeProfileError(caught));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        question ? (
          <View style={{ gap: 12 }}>
            {error ? <Notice tone="danger">{error}</Notice> : null}
            <Button
              label={step === questions.length - 1 ? "See my result" : "Next"}
              onPress={next}
              disabled={!answers[question.code]}
              loading={saving}
              testID="risk-next"
            />
          </View>
        ) : risk?.latest ? (
          <View style={{ gap: 12 }}>
            <Button label="Done" onPress={() => router.navigate("/profile")} testID="risk-done" />
            <TextLink
              label="Answer again"
              onPress={() => {
                setStep(0);
                setError(null);
              }}
              testID="risk-again"
            />
          </View>
        ) : null
      }
    >
      <Stack.Screen options={{ title: "About your investing" }} />
      <BackButton
        fallback="/profile"
        onPress={step !== null && step > 0 ? () => setStep(step - 1) : undefined}
      />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>About your investing</Eyebrow>
      </View>

      {state.status === "loading" ? (
        <View style={{ marginTop: 40 }}>
          <ActivityIndicator accessibilityLabel="Loading" color={colors.black} />
        </View>
      ) : null}

      {state.status === "error" ? (
        <View style={{ marginTop: 22, gap: 12 }}>
          <Title>About your investing</Title>
          <Notice tone="danger">{state.message}</Notice>
          <Button label="Try again" onPress={() => setAttempt((n) => n + 1)} height={52} />
        </View>
      ) : null}

      {question && copy ? (
        <View style={{ marginTop: 12, gap: 14 }}>
          <StepProgress step={(step ?? 0) + 1} of={questions.length} />
          <Title>{copy.title}</Title>
          <Txt color={colors.muted}>
            {`Four short questions, so we can suggest investments that suit you. Question ${(step ?? 0) + 1} of ${questions.length}.`}
          </Txt>
          <View style={{ gap: 10 }} accessibilityRole="radiogroup" accessibilityLabel={copy.title}>
            {question.options.map((code) => (
              <Choice
                key={code}
                label={copy.options[code]?.label ?? code}
                detail={copy.options[code]?.detail}
                selected={answers[question.code] === code}
                onPress={() => setAnswers({ ...answers, [question.code]: code })}
                testID={`risk-${question.code}-${code}`}
              />
            ))}
          </View>
          <View style={styles.info}>
            <Txt size={fontSizes.sm}>
              There are no wrong answers. You can answer again any time in Profile, and we ask again once a year.
            </Txt>
          </View>
        </View>
      ) : null}

      {step === null && risk?.latest ? (
        <View style={{ marginTop: 12, gap: 14 }} testID="risk-result">
          <Title>{`You are a ${PROFILE_COPY[risk.latest.profile].name.toLowerCase()} investor`}</Title>
          <Card>
            <View style={{ gap: 8 }}>
              <View style={styles.badge}>
                <Txt size={12} weight="bold" color={colors.ink}>
                  {PROFILE_COPY[risk.latest.profile].name}
                </Txt>
              </View>
              <Txt>{PROFILE_COPY[risk.latest.profile].about}</Txt>
              <Txt size={fontSizes.sm} color={colors.muted}>
                {`Answered on ${longDate(risk.latest.answeredAt)}. We will ask again in a year.`}
              </Txt>
            </View>
          </Card>
          <Card padded={false}>
            {questions.map((q, index) => (
              <View key={q.code} style={[styles.answer, index > 0 && styles.divider]}>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  {QUESTION_COPY[q.code]?.title ?? q.code}
                </Txt>
                <Txt weight="semibold">
                  {QUESTION_COPY[q.code]?.options[risk.latest?.answers[q.code] ?? ""]?.label ?? "Not answered"}
                </Txt>
              </View>
            ))}
          </Card>
          <Txt size={fontSizes.sm} color={colors.muted}>
            This guides what we suggest. It never stops you choosing an investment yourself.
          </Txt>
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  info: { backgroundColor: colors.surfaceSunken, borderRadius: radii.md, paddingVertical: 12, paddingHorizontal: 14 },
  badge: {
    alignSelf: "flex-start",
    backgroundColor: colors.lime,
    borderRadius: radii.pill,
    paddingVertical: 3,
    paddingHorizontal: 10,
  },
  answer: { paddingVertical: 12, paddingHorizontal: 16, gap: 2 },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
});
