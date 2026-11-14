import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Txt } from "@/components/Txt";
import { formatDateTime, formatMoney, networkName, networkProduct } from "@/payments/format";
import { Detail, DetailList, Loading, ShareNote } from "@/payments/Parts";
import { asText, shareText, ShareOutcome } from "@/payments/share";
import { useDeposit } from "@/payments/useDeposit";
import { colors, fontSizes } from "@/theme";

/** The receipt for a confirmed payment. Anything not yet confirmed goes back to the status screen. */
export default function Receipt() {
  const router = useRouter();
  const { depositId } = useLocalSearchParams<{ depositId: string }>();
  const { deposit, missing, stale } = useDeposit(depositId);
  const done = () => router.replace("/");
  const [shared, setShared] = useState<ShareOutcome | null>(null);

  if (missing) {
    return (
      <Screen signedIn footer={<Button label="Close" onPress={done} />}>
        <Stack.Screen options={{ title: "Receipt" }} />
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">We could not find this payment.</Notice>
        </View>
      </Screen>
    );
  }

  if (!deposit) {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Receipt" }} />
        {stale ? (
          <Notice tone="neutral">We could not reach Finvest. Trying again.</Notice>
        ) : (
          <Loading label="Loading your receipt" />
        )}
      </Screen>
    );
  }

  if (deposit.status !== "confirmed" || !deposit.confirmedAt) {
    return <Redirect href={`/pay/deposit/${deposit.id}`} />;
  }

  const network = networkName[deposit.network];
  const rows: Detail[] = [
    { label: "Date and time", value: formatDateTime(deposit.confirmedAt) },
    { label: "Paid from", value: networkProduct[deposit.network] },
    { label: "Number", value: deposit.payer },
  ];
  if (deposit.networkReference) {
    rows.push({ label: `${network} reference`, value: deposit.networkReference });
  }
  rows.push({ label: "Finvest reference", value: deposit.reference });
  // The number is already masked as the PDF receipt masks it.
  const share = async () =>
    setShared(await shareText("Finvest receipt", asText(`Finvest receipt: ${formatMoney(deposit.amount)} paid into ${deposit.payable.title}`, rows)));

  return (
    <Screen
      signedIn
      footer={
        <>
          <Button label="Share receipt" variant="secondary" onPress={share} testID="receipt-share" />
          <Button label="Done" onPress={done} testID="receipt-done" />
          <TextLink label="Something wrong with this?" onPress={() => router.push("/help")} />
        </>
      }
    >
      <Stack.Screen options={{ title: "Receipt" }} />
      <View style={styles.top}>
        <View style={styles.tick}>
          <Icon name="check" size={30} color={colors.black} strokeWidth={2.6} />
        </View>
        <Txt size={fontSizes.sm} weight="bold" color={colors.success}>
          Completed
        </Txt>
        <Txt
          accessibilityRole="header"
          size={34}
          weight="extrabold"
          lineHeight={40}
          style={{ letterSpacing: -0.8 }}
        >
          {formatMoney(deposit.amount)}
        </Txt>
        <Txt color={colors.muted}>{`Paid into ${deposit.payable.title}`}</Txt>
      </View>
      <View style={{ marginTop: 24 }}>
        <DetailList rows={rows} />
      </View>
      <ShareNote outcome={shared} />
      <View style={{ marginTop: 16, gap: 4 }}>
        <Txt size={fontSizes.sm} color={colors.muted}>
          We also email you this receipt as a PDF and keep it in Documents.
        </Txt>
        <TextLink label="Open Documents" onPress={() => router.push("/documents")} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { marginTop: 32, alignItems: "center", gap: 6 },
  tick: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 6,
  },
});
