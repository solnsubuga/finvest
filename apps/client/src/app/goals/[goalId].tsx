import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Icon } from "@/components/Icon";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useGoals } from "@/goals/GoalsProvider";
import { goalSubtitle, ProgressBar, StandingBadge } from "@/goals/Parts";
import { savingLine } from "@/goals/savings";
import { describeGoalError, Goal, Saving } from "@/goals/types";
import { formatDay, formatMoney } from "@/payments/format";
import { DetailList, Loading } from "@/payments/Parts";
import { colors } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; goal: Goal }
  | { status: "error"; message: string };

export default function GoalDetail() {
  const router = useRouter();
  const client = useGoals();
  const { goalId } = useLocalSearchParams<{ goalId: string }>();
  const [state, setState] = useState<State>({ status: "loading" });
  const [confirmClose, setConfirmClose] = useState(false);
  const [closing, setClosing] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);

  const load = useCallback(() => {
    let live = true;
    client.getGoal(goalId).then(
      (goal) => live && setState({ status: "ready", goal }),
      (caught) => live && setState({ status: "error", message: describeGoalError(caught) }),
    );
    return () => {
      live = false;
    };
  }, [client, goalId]);

  useFocusEffect(load);

  const close = async () => {
    setClosing(true);
    setCloseError(null);
    try {
      const goal = await client.closeGoal(goalId);
      setState({ status: "ready", goal });
      setConfirmClose(false);
    } catch (caught) {
      setCloseError(describeGoalError(caught));
    } finally {
      setClosing(false);
    }
  };

  const goal = state.status === "ready" ? state.goal : null;
  const open = goal?.status === "active";

  return (
    <Screen
      signedIn
      footer={
        open ? (
          <Button
            label="Save now"
            onPress={() => router.push(`/goals/save/${goalId}`)}
            variant="lime"
            testID="save-now"
          />
        ) : null
      }
    >
      <BackButton fallback="/goals" />
      {state.status === "loading" ? (
        <Loading label="Loading your goal" />
      ) : state.status === "error" ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">{state.message}</Notice>
        </View>
      ) : goal ? (
        <View style={{ marginTop: 16, gap: 14 }}>
          <View style={{ gap: 4 }}>
            <Title>{goal.name}</Title>
            <Txt size={13} color={colors.muted}>
              {goalSubtitle(goal)}
            </Txt>
          </View>

          <View style={styles.hero}>
            <View style={styles.heroHead}>
              <Txt size={13} color={colors.onBlackMuted}>
                Saved so far
              </Txt>
              <StandingBadge goal={goal} onDark />
            </View>
            <View style={styles.amounts}>
              <Txt size={34} weight="extrabold" lineHeight={40} color={colors.white} style={{ letterSpacing: -0.8 }}>
                {formatMoney(goal.progress.value)}
              </Txt>
              <Txt size={13} color={colors.onBlackMuted}>
                {`of ${formatMoney(goal.target).replace("UGX ", "")}`}
              </Txt>
            </View>
            <ProgressBar goal={goal} onDark />
            <View style={styles.split}>
              <Txt size={13} color={colors.white}>
                {`You saved ${formatMoney({ currency: "UGX", minor: goal.progress.value.minor - goal.progress.interest.minor }).replace("UGX ", "")}`}
              </Txt>
              <Txt size={13} weight="bold" color={colors.lime}>
                {`Interest added ${formatMoney(goal.progress.interest).replace("UGX ", "")}`}
              </Txt>
            </View>
          </View>

          {goal.progress.awaitingPayment.minor > 0 ? (
            <Notice tone="warning">
              {`${formatMoney(goal.progress.awaitingPayment)} is waiting for your payment. It counts once it is paid.`}
            </Notice>
          ) : null}

          <Eyebrow>Where this money is working</Eyebrow>
          {goal.savings.length === 0 ? (
            <Notice tone="neutral">
              Nothing saved yet. Your first saving is invested the day it is paid.
            </Notice>
          ) : (
            <Card padded={false}>
              {goal.savings.map((saving, index) => (
                <SavingRow
                  key={saving.investmentId}
                  saving={saving}
                  first={index === 0}
                  onPay={() => router.push(`/pay/${saving.investmentId}`)}
                  onOpen={() => router.push(`/investment/${saving.investmentId}`)}
                />
              ))}
            </Card>
          )}

          {open && goal.savings.some((saving) => saving.status === "active") ? (
            <Txt size={12} color={colors.muted} testID="goal-take-out-note">
              Need some of it sooner? Open a saving to take it out early. Each saving comes out
              whole, with the interest it has earned after tax and no fee, and stops counting towards
              this goal.
            </Txt>
          ) : null}

          <DetailList rows={facts(goal)} />

          {open ? (
            <TextLink
              label="Change name, target or date"
              onPress={() => router.push(`/goals/edit/${goalId}`)}
              testID="goal-edit"
            />
          ) : null}

          {open ? (
            confirmClose ? (
              <View style={styles.confirm} testID="close-confirm">
                <Txt weight="bold">Close this goal?</Txt>
                <Txt size={13} color={colors.muted}>
                  Your investments keep running and earning, and stay in your portfolio. Nothing more is
                  saved to this goal, and a closed goal cannot be opened again.
                </Txt>
                {closeError ? <Notice tone="danger">{closeError}</Notice> : null}
                <Button label="Close goal" onPress={close} loading={closing} height={48} testID="close-yes" />
                <TextLink label="Keep it" onPress={() => setConfirmClose(false)} underline={false} />
              </View>
            ) : (
              <TextLink label="Close this goal" onPress={() => setConfirmClose(true)} />
            )
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

function facts(goal: Goal) {
  const rows = [{ label: "Target", value: formatMoney(goal.target) }];
  if (goal.status === "closed") {
    rows.push({ label: "Status", value: "Closed" });
    return rows;
  }
  if (goal.progress.standing === "reached") {
    rows.push({ label: "Status", value: "Target reached" });
  } else if (goal.plan) {
    rows.push({ label: "Save each month", value: formatMoney(goal.plan.saving) });
    rows.push({ label: "Invested in", value: `${goal.plan.productName}, rolled over` });
    rows.push({ label: "Rate after tax", value: `${goal.plan.ratePercent}% a year` });
  } else {
    rows.push({ label: "Save", value: "Any amount from UGX 100,000" });
  }
  if (goal.targetDate) {
    rows.push({ label: "Needed by", value: formatDay(goal.targetDate) });
  }
  return rows;
}

function SavingRow({
  saving,
  first,
  onPay,
  onOpen,
}: {
  saving: Saving;
  first: boolean;
  onPay: () => void;
  onOpen: () => void;
}) {
  const waiting = saving.status === "awaiting_payment";
  const counts = saving.status === "active";
  const line = waiting ? "Waiting for your payment" : savingLine(saving);
  const body = (
    <>
      <View style={[styles.dot, { backgroundColor: counts ? colors.black : colors.neutral }]} />
      <View style={{ flex: 1 }}>
        <Txt size={14} weight="bold">
          {saving.productName}
        </Txt>
        <Txt size={12} color={colors.muted}>
          {line}
        </Txt>
      </View>
    </>
  );
  if (waiting) {
    return (
      <View style={[styles.row, !first && styles.divider]}>
        {body}
        {/* Its own width, so the label is never squeezed by a long product name. */}
        <View style={{ minWidth: 88 }}>
          <Button label="Pay" onPress={onPay} height={40} variant="primary" testID={`pay-${saving.investmentId}`} />
        </View>
      </View>
    );
  }
  const amount = formatMoney(saving.value).replace("UGX ", "");
  return (
    <Pressable
      style={[styles.row, !first && styles.divider]}
      onPress={onOpen}
      accessibilityRole="link"
      accessibilityLabel={`${saving.productName}. ${line}. ${formatMoney(saving.value)}`}
      testID={`saving-${saving.investmentId}`}
    >
      {body}
      <Txt size={14} weight="bold" color={counts ? colors.ink : colors.muted}>
        {amount}
      </Txt>
      <Icon name="chevron" size={16} strokeWidth={2.2} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  hero: { backgroundColor: colors.black, borderRadius: 24, padding: 20, gap: 8 },
  heroHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  amounts: { flexDirection: "row", alignItems: "baseline", gap: 8, flexWrap: "wrap" },
  split: { flexDirection: "row", justifyContent: "space-between", flexWrap: "wrap", gap: 6 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, paddingHorizontal: 16 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  dot: { width: 10, height: 10, borderRadius: 5 },
  confirm: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 16,
    gap: 10,
  },
});
