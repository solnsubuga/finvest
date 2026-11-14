import { useCallback } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { LoadFailed } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Eyebrow, Txt } from "@/components/Txt";
import { formatDay } from "@/investing/format";
import { formatDateTime, formatMoney } from "@/payments/format";
import { DetailList, Loading } from "@/payments/Parts";
import { accountLabel, payoutCopy } from "@/payouts/format";
import { Pill, useFocusLoaded } from "@/payouts/Parts";
import { usePayouts } from "@/payouts/PayoutsProvider";
import { Payout, PayoutAccount } from "@/payouts/types";
import { colors, fontSizes } from "@/theme";

type Read = { payout: Payout; account: PayoutAccount | null };

/** One payout: how much, where to, and how it stands. */
export default function PayoutDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = usePayouts();
  const [loaded, retry] = useFocusLoaded(
    useCallback(async (): Promise<Read> => {
      const payout = await client.getPayout(id);
      // A removed account is no longer listed, but its payout still is.
      const accounts = await client.listAccounts();
      return { payout, account: accounts.find((account) => account.id === payout.destinationId) ?? null };
    }, [client, id]),
  );

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Payout" }} />
      <BackButton fallback="/payouts/history" />
      {loaded.state === "loading" ? (
        <Loading label="Loading your payout" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <LoadFailed message={loaded.message} onRetry={retry} />
        </View>
      ) : (
        <Body {...loaded.value} />
      )}
    </Screen>
  );
}

function Body({ payout, account }: Read) {
  const router = useRouter();
  const copy = payoutCopy(payout);
  const returned = payout.returnedAt !== null;
  return (
    <View style={{ marginTop: 20, gap: 16 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
        <Eyebrow>Payout</Eyebrow>
        <Pill label={copy.label} tone={copy.tone} />
      </View>
      <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
          {returned ? "Came back to us" : payout.status === "paid" ? "You were paid" : "To you"}
        </Txt>
        <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }} testID="payout-amount">
          {formatMoney(payout.amount)}
        </Txt>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
          {returned ? "Waiting to be sent again" : copy.label}
        </Txt>
      </View>
      <DetailList
        rows={[
          { label: "To", value: account ? accountLabel(account) : payout.kind === "bank" ? "A bank account you removed" : "A mobile money number you removed" },
          { label: "Asked for", value: formatDay(payout.businessDate) },
          { label: "Started", value: formatDateTime(payout.createdAt) },
          ...(payout.returnedAt ? [{ label: "Came back", value: formatDateTime(payout.returnedAt) }] : []),
        ]}
      />
      <Notice tone={payout.status === "failed" || returned ? "warning" : "neutral"}>{copy.detail}</Notice>
      {returned ? (
        <Button
          label="Send it again"
          onPress={() => router.push(`/payouts/payout/resend/${payout.id}`)}
          testID="payout-resend"
        />
      ) : null}
    </View>
  );
}
