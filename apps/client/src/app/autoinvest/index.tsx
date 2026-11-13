import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";

import { useAutoinvest } from "@/autoinvest/AutoinvestProvider";
import { PlanCard } from "@/autoinvest/Parts";
import { describeAutoinvestError, Plan } from "@/autoinvest/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; plans: Plan[] };

/** Every plan the client has that is not stopped, and the way to start one. */
export default function Plans() {
  const client = useAutoinvest();
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(() => {
    client.listPlans().then(
      (plans) => setState({ status: "ready", plans }),
      (caught) => setState({ status: "error", message: describeAutoinvestError(caught) }),
    );
  }, [client]);

  // Reload on return from a new plan or from pausing one.
  useFocusEffect(load);

  return (
    <Screen
      signedIn
      footer={
        state.status === "ready" ? (
          <Button
            label={state.plans.length ? "Start another plan" : "Start a plan"}
            variant={state.plans.length ? "secondary" : "primary"}
            onPress={() => router.push("/autoinvest/new")}
            testID="plan-new"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Invest automatically" }} />
      <BackButton fallback="/invest" />
      <View style={{ marginTop: 20, gap: 8 }}>
        <Title>Invest automatically</Title>
        <Txt size={fontSizes.md} color={colors.muted}>
          Choose an amount and a day. On that day we send a prompt to your phone, you approve it
          with your mobile money PIN, and the money starts earning.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 12 }}>
        {state.status === "loading" ? (
          <Loading label="Loading your plans" />
        ) : state.status === "error" ? (
          <>
            <Notice tone="danger">{state.message}</Notice>
            <Button label="Try again" variant="secondary" onPress={load} testID="plans-retry" />
          </>
        ) : state.plans.length === 0 ? (
          <Card>
            <View style={{ gap: 10 }} testID="plans-empty">
              <Txt size={fontSizes.lg} weight="extrabold">
                No plans yet
              </Txt>
              <Bullet>Every week or every month, on the day you choose.</Bullet>
              <Bullet>Each payment is a new investment at that day&apos;s rate.</Bullet>
              <Bullet>
                Nothing is taken without your PIN, and you can pause or stop at any time.
              </Bullet>
            </View>
          </Card>
        ) : (
          state.plans.map((plan) => (
            <PlanCard
              key={plan.id}
              plan={plan}
              onPress={() => router.push(`/autoinvest/${plan.id}`)}
            />
          ))
        )}
      </View>
    </Screen>
  );
}

function Bullet({ children }: { children: string }) {
  return (
    <View style={styles.bullet}>
      <View style={styles.dot} />
      <Txt size={14} color={colors.ink} style={{ flex: 1 }}>
        {children}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  bullet: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.lime,
    marginTop: 7,
  },
});
