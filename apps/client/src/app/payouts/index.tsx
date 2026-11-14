import { useCallback } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { LinkCard } from "@/components/LinkCard";
import { LoadFailed } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { Loading } from "@/payments/Parts";
import { AccountCard, useFocusLoaded } from "@/payouts/Parts";
import { usePayouts } from "@/payouts/PayoutsProvider";
import { colors, fontSizes } from "@/theme";

/** Where the client can be paid: every account not removed, and the way to add one. */
export default function PayoutAccounts() {
  const client = usePayouts();
  const router = useRouter();
  const [loaded, retry] = useFocusLoaded(useCallback(() => client.listAccounts(), [client]));

  return (
    <Screen
      signedIn
      footer={
        loaded.state === "ready" ? (
          <Button
            label={loaded.value.length ? "Add another account" : "Add an account"}
            variant={loaded.value.length ? "secondary" : "primary"}
            onPress={() => router.push("/payouts/add")}
            testID="payout-account-add"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Payout accounts" }} />
      <BackButton fallback="/profile" />
      <View style={{ marginTop: 20, gap: 8 }}>
        <Title>Payout accounts</Title>
        <Txt size={fontSizes.md} color={colors.muted}>
          Where we send your money when an investment ends or you withdraw. We only pay accounts in the name on your
          ID.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 12 }}>
        {loaded.state === "loading" ? (
          <Loading label="Loading your payout accounts" />
        ) : loaded.state === "failed" ? (
          <LoadFailed message={loaded.message} onRetry={retry} />
        ) : loaded.value.length === 0 ? (
          <Card>
            <View style={{ gap: 10 }} testID="payout-accounts-empty">
              <Txt size={fontSizes.lg} weight="extrabold">
                No payout accounts yet
              </Txt>
              <Bullet>Add your MTN or Airtel mobile money number, or your bank account.</Bullet>
              <Bullet>We text you a code to prove the number or account is yours.</Bullet>
              <Bullet>For a bank account, send a statement, a bank letter or a photo of the card.</Bullet>
              <Bullet>We check it is in your name. It can be paid one day after we approve it.</Bullet>
            </View>
          </Card>
        ) : (
          loaded.value.map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              nowMs={loaded.readAtMs}
              onPress={() => router.push(`/payouts/${account.id}`)}
            />
          ))
        )}
      </View>
      <View style={{ marginTop: 28, gap: 10 }}>
        <Eyebrow>Money paid out</Eyebrow>
        <LinkCard
          icon="send"
          title="Payouts"
          description="What we have sent you, and your withdrawal limits"
          onPress={() => router.push("/payouts/history")}
          testID="open-payouts"
        />
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
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.lime, marginTop: 7 },
});
