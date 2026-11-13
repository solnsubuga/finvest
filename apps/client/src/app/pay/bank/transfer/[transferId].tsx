import { StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { formatDateTime, formatMoney, formatTime, mismatchCopy } from "@/payments/format";
import { AmountCard, DetailList, Loading, Timeline } from "@/payments/Parts";
import { useBankTransfer } from "@/payments/useDeposit";
import { colors, fontSizes } from "@/theme";

/**
 * Pay by bank transfer, step 3: where the receipt stands. One address for the
 * whole life of the transfer, whatever staff decide.
 */
export default function BankTransferStatus() {
  const router = useRouter();
  const { transferId } = useLocalSearchParams<{ transferId: string }>();
  const { transfer, missing, stale } = useBankTransfer(transferId);
  const close = () => router.replace("/");

  if (missing) {
    return (
      <Screen signedIn footer={<Button label="Close" onPress={close} />}>
        <Stack.Screen options={{ title: "Bank transfer" }} />
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">We could not find this payment.</Notice>
        </View>
      </Screen>
    );
  }

  if (!transfer) {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Bank transfer" }} />
        {stale ? (
          <Notice tone="neutral">We could not reach Finvest. Trying again.</Notice>
        ) : (
          <Loading label="Loading your bank transfer" />
        )}
      </Screen>
    );
  }

  if (transfer.status === "confirmed" && transfer.confirmedAt) {
    return (
      <Screen signedIn footer={<Button label="Done" onPress={close} testID="transfer-done" />}>
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
            {formatMoney(transfer.amountSent)}
          </Txt>
          <Txt color={colors.muted}>{`Paid into ${transfer.payable.title}`}</Txt>
        </View>
        <View style={{ marginTop: 24 }}>
          <DetailList
            rows={[
              { label: "Confirmed", value: formatDateTime(transfer.confirmedAt) },
              { label: "Paid by", value: "Bank transfer" },
              { label: "Reference", value: transfer.reference },
            ]}
          />
        </View>
      </Screen>
    );
  }

  if (transfer.status === "not_matched") {
    return (
      <Screen
        signedIn
        footer={
          <>
            <Button
              label="Upload another receipt"
              onPress={() => router.replace(`/pay/bank/receipt/${transfer.payable.id}`)}
              testID="transfer-replace"
            />
            <TextLink label="Close" onPress={close} />
          </>
        }
      >
        <Stack.Screen options={{ title: "We could not match your receipt" }} />
        <View style={{ marginTop: 20, gap: 10 }} accessibilityLiveRegion="polite">
          <Title>We could not match your receipt</Title>
          <Txt color={colors.muted}>{mismatchCopy[transfer.reason ?? "other"]}</Txt>
        </View>
        <View style={{ marginTop: 20, gap: 16 }}>
          <Notice tone="neutral">
            No investment was started. If you did send the money, it is safe with our bank while we
            look for it. Upload a new receipt, or ask for help and we will trace it.
          </Notice>
          <DetailList
            rows={[
              { label: "You said you sent", value: formatMoney(transfer.amountSent) },
              { label: "For", value: transfer.payable.title },
              { label: "Reference", value: transfer.reference },
              { label: "Receipt sent", value: formatDateTime(transfer.submittedAt) },
            ]}
          />
        </View>
      </Screen>
    );
  }

  return (
    <Screen signedIn footer={<TextLink label="Close" onPress={close} />}>
      <Stack.Screen options={{ title: "Bank transfer status" }} />
      <View style={{ marginTop: 20, gap: 10 }} accessibilityLiveRegion="polite">
        <Title>We have your receipt</Title>
        <Txt color={colors.muted}>
          We are matching it to our bank statement. You can leave this screen.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 16 }}>
        {stale ? (
          <Notice tone="neutral">We could not refresh this just now. Trying again.</Notice>
        ) : null}
        <AmountCard
          title={`Bank transfer, reference ${transfer.reference}`}
          amount={transfer.amountSent}
          note={`For your ${transfer.payable.title}`}
        />
        <Timeline
          steps={[
            { label: "Receipt received", state: "done", note: formatTime(transfer.submittedAt) },
            { label: "Matching it to our bank statement", state: "now" },
            { label: "Your investment starts", state: "later" },
          ]}
        />
        <Txt size={fontSizes.sm} color={colors.muted}>
          If we cannot match it, we tell you why in plain words, for example that the amount on the
          receipt is different, and you can upload a new one here.
        </Txt>
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
