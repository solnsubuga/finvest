import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { DocumentLink, useDocumentFor } from "@/documents/DocumentLink";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Txt } from "@/components/Txt";
import { formatDateTime, formatMoney, networkName, networkProduct } from "@/payments/format";
import { Detail, DetailList, Loading } from "@/payments/Parts";
import { useDeposit } from "@/payments/useDeposit";
import { colors, fontSizes } from "@/theme";

/** The receipt for a confirmed payment. Anything not yet confirmed goes back to the status screen. */
export default function Receipt() {
  const router = useRouter();
  const { depositId } = useLocalSearchParams<{ depositId: string }>();
  const { deposit, missing, stale } = useDeposit(depositId);
  const pdf = useDocumentFor("receipt", depositId);
  const done = () => router.replace("/");

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

  return (
    <Screen signedIn footer={<Button label="Done" onPress={done} testID="receipt-done" />}>
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
      {pdf ? (
        <View style={{ marginTop: 12 }}>
          <DocumentLink document={pdf} title="Receipt (PDF)" testID="receipt-pdf" />
        </View>
      ) : null}
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
