import { useCallback } from "react";
import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card } from "@/components/Blocks";
import { LoadFailed } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatMoney } from "@/payments/format";
import { DetailList, Loading } from "@/payments/Parts";
import { PayoutCard, useFocusLoaded } from "@/payouts/Parts";
import { usePayouts } from "@/payouts/PayoutsProvider";
import { Limits, Payout, PayoutAccount } from "@/payouts/types";
import { colors, fontSizes } from "@/theme";

type Read = { payouts: Payout[]; accounts: PayoutAccount[]; limits: Limits };

/** Every payout the client has had, newest first, and the withdrawal limits in force today. */
export default function Payouts() {
  const client = usePayouts();
  const router = useRouter();
  const [loaded, retry] = useFocusLoaded(
    useCallback(async (): Promise<Read> => {
      const [payouts, accounts, limits] = await Promise.all([client.listPayouts(), client.listAccounts(), client.getLimits()]);
      return { payouts, accounts, limits };
    }, [client]),
  );

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Payouts" }} />
      <BackButton fallback="/payouts" />
      <View style={{ marginTop: 20, gap: 8 }}>
        <Title>Payouts</Title>
        <Txt size={fontSizes.md} color={colors.muted}>
          Money we have sent you when an investment ended or you withdrew.
        </Txt>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your payouts" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <LoadFailed message={loaded.message} onRetry={retry} />
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 12 }}>
          {loaded.value.payouts.length === 0 ? (
            <Card>
              <View style={{ gap: 6 }} testID="payouts-empty">
                <Txt size={fontSizes.lg} weight="extrabold">
                  No payouts yet
                </Txt>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  When an investment you chose to be paid out ends, or you withdraw one early, the money shows here
                  until it reaches your account.
                </Txt>
              </View>
            </Card>
          ) : (
            loaded.value.payouts.map((payout) => (
              <PayoutCard
                key={payout.id}
                payout={payout}
                account={loaded.value.accounts.find((account) => account.id === payout.destinationId)}
                onPress={() => router.push(`/payouts/payout/${payout.id}`)}
              />
            ))
          )}
          <View style={{ marginTop: 16, gap: 10 }}>
            <Eyebrow>Withdrawal limits</Eyebrow>
            <DetailList rows={limitRows(loaded.value.limits)} />
            <Txt size={fontSizes.xs} color={colors.muted}>
              Larger amounts go to a bank account. If you signed in with a texted code in place of a passkey, or reset
              your password, in the last 24 hours, we pay only an account that has been paid before.
            </Txt>
          </View>
        </View>
      )}
    </Screen>
  );
}

function limitRows(limits: Limits) {
  const shown = (money: Limits["bankPerDay"]) => (money ? formatMoney(money) : "No limit");
  return [
    { label: "Mobile money, each payout", value: formatMoney(limits.mobileMoneyPerPayout) },
    { label: "Mobile money, each day", value: shown(limits.mobileMoneyPerDay) },
    { label: "Bank, each payout", value: shown(limits.bankPerPayout) },
    { label: "Bank, each day", value: shown(limits.bankPerDay) },
  ];
}
