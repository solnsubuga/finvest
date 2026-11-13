import { useCallback, useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";

import { formatE164 } from "@/auth/phone";
import { useAutoinvest } from "@/autoinvest/AutoinvestProvider";
import { StatusPill } from "@/autoinvest/Parts";
import { formatLongDay, runCopy, runReason, scheduleLabel } from "@/autoinvest/schedule";
import { describeAutoinvestError, Plan, Run } from "@/autoinvest/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatMoney, networkProduct } from "@/payments/format";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/** One plan: what it does, when it runs next, what each run did, and pause, resume or stop. */
export default function PlanDetail() {
  const { planId } = useLocalSearchParams<{ planId: string }>();
  const client = useAutoinvest();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const fetch = useCallback(
    () =>
      client
        .getPlan(planId)
        .then(setPlan, (caught) => setLoadError(describeAutoinvestError(caught))),
    [client, planId],
  );

  useEffect(() => {
    void fetch();
  }, [fetch]);

  const load = () => {
    setLoadError(null);
    void fetch();
  };

  const act = async (action: (id: string) => Promise<Plan>) => {
    if (!plan) {
      return;
    }
    setError(null);
    setBusy(true);
    try {
      setPlan(await action(plan.id));
      setConfirmStop(false);
    } catch (caught) {
      setError(describeAutoinvestError(caught));
    } finally {
      setBusy(false);
    }
  };

  const footer =
    !plan || plan.status === "stopped" ? null : confirmStop ? (
      <View style={{ gap: 4 }}>
        <Button
          label="Yes, stop this plan"
          onPress={() => act(client.stopPlan)}
          loading={busy}
          testID="plan-stop-confirm"
        />
        <TextLink label="Keep it" onPress={() => setConfirmStop(false)} underline={false} />
      </View>
    ) : (
      <View style={{ gap: 4 }}>
        {plan.status === "active" ? (
          <Button
            label="Pause plan"
            variant="secondary"
            onPress={() => act(client.pausePlan)}
            loading={busy}
            testID="plan-pause"
          />
        ) : (
          <Button
            label="Resume plan"
            variant="lime"
            onPress={() => act(client.resumePlan)}
            loading={busy}
            testID="plan-resume"
          />
        )}
        <TextLink label="Stop plan" onPress={() => setConfirmStop(true)} underline={false} />
      </View>
    );

  return (
    <Screen signedIn footer={footer}>
      <Stack.Screen options={{ title: "Your plan" }} />
      <BackButton fallback="/autoinvest" />
      {loadError ? (
        <View style={{ marginTop: 20, gap: 12 }}>
          <Notice tone="danger">{loadError}</Notice>
          <Button label="Try again" variant="secondary" onPress={load} />
        </View>
      ) : !plan ? (
        <Loading label="Loading your plan" />
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <View style={styles.heading}>
            <Title>{formatMoney(plan.amount)}</Title>
            <StatusPill status={plan.status} />
          </View>
          <Txt size={fontSizes.md} color={colors.muted}>
            {`${scheduleLabel(plan.frequency, plan.day)}, into the ${plan.product.name}.`}
          </Txt>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          {confirmStop ? (
            <Notice tone="warning">
              Stopping ends this plan for good. Investments it already made keep earning until they
              mature. You can start a new plan at any time.
            </Notice>
          ) : null}
          <View style={styles.next} testID="plan-next">
            {plan.status === "active" && plan.nextRunOn ? (
              <>
                <Txt size={13} color={colors.onBlackMuted}>
                  Next prompt
                </Txt>
                <Txt size={24} weight="extrabold" color={colors.white}>
                  {formatLongDay(plan.nextRunOn)}
                </Txt>
                <Txt size={13} color={colors.onBlackMuted}>
                  {`Approve it on ${formatE164(plan.payer)} with your mobile money PIN.`}
                </Txt>
              </>
            ) : (
              <Txt size={15} weight="bold" color={colors.white}>
                {plan.status === "paused"
                  ? "Paused. No prompts until you resume, and the days in between are not made up."
                  : "Stopped. This plan will not run again."}
              </Txt>
            )}
          </View>
          <DetailList
            rows={[
              { label: "Each time", value: formatMoney(plan.amount) },
              { label: "When", value: scheduleLabel(plan.frequency, plan.day) },
              { label: "Into", value: plan.product.name },
              {
                label: "At maturity",
                value: plan.atMaturity === "roll_over" ? "Roll over" : "Pay out",
              },
              { label: "Pay from", value: networkProduct[plan.network] },
            ]}
          />
          <View style={{ gap: 8 }}>
            <Eyebrow>Past payments</Eyebrow>
            {plan.runs.length === 0 ? (
              <Card>
                <Txt size={14} color={colors.muted}>
                  Nothing yet. Each payment will be listed here with what happened to it.
                </Txt>
              </Card>
            ) : (
              <Card padded={false}>
                {plan.runs.map((run, index) => (
                  <RunRow
                    key={run.id}
                    run={run}
                    amount={formatMoney(plan.amount)}
                    first={index === 0}
                  />
                ))}
              </Card>
            )}
          </View>
        </View>
      )}
    </Screen>
  );
}

function RunRow({ run, amount, first }: { run: Run; amount: string; first: boolean }) {
  const reason = runReason(run.code, run.attempt, run.status);
  const done = run.status === "paid";
  return (
    <View style={[styles.run, !first && styles.divider]} accessible>
      <View
        style={[
          styles.dot,
          {
            backgroundColor: done
              ? colors.lime
              : run.status === "failed"
                ? colors.danger
                : colors.neutral,
          },
        ]}
      />
      <View style={{ flex: 1 }}>
        <Txt size={14} weight="bold">
          {formatLongDay(run.dueOn)}
        </Txt>
        <Txt size={12} color={colors.muted}>
          {reason ? `${runCopy[run.status]}. ${reason}` : runCopy[run.status]}
        </Txt>
      </View>
      <Txt size={14} weight="bold" color={done ? colors.ink : colors.muted}>
        {done ? amount : "Not collected"}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  heading: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  next: {
    backgroundColor: colors.black,
    borderRadius: 22,
    padding: 18,
    gap: 4,
  },
  run: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 13,
    paddingHorizontal: 16,
  },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  dot: { width: 10, height: 10, borderRadius: 5 },
});
