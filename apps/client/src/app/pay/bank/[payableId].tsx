import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { formatMoney } from "@/payments/format";
import { Loading } from "@/payments/Parts";
import { usePayments } from "@/payments/PaymentsProvider";
import { BankInstructions, describePaymentError, openPaymentHref } from "@/payments/types";
import { colors, fontSizes } from "@/theme";

/** One bank detail with its own Copy button, because these get typed into a banking app. */
function CopyRow({ label, value, first }: { label: string; value: string; first: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await Clipboard.setStringAsync(value.replace(/\s/g, ""));
    setCopied(true);
    setTimeout(() => setCopied(false), 2_000);
  };
  return (
    <View style={[styles.row, !first && styles.divider]}>
      <View style={{ flex: 1 }}>
        <Txt size={fontSizes.xs} color={colors.muted}>
          {label}
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {value}
        </Txt>
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Copy ${label.toLowerCase()}`}
        onPress={copy}
        style={({ pressed }) => [styles.copy, pressed && { opacity: 0.7 }]}
      >
        <Txt size={fontSizes.sm} weight="bold" accessibilityLiveRegion="polite">
          {copied ? "Copied" : "Copy"}
        </Txt>
      </Pressable>
    </View>
  );
}

/** Pay by bank transfer, step 1: where to send the money and what to put as the reference. */
export default function BankDetails() {
  const router = useRouter();
  const { payableId } = useLocalSearchParams<{ payableId: string }>();
  const { client } = usePayments();
  const [instructions, setInstructions] = useState<BankInstructions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    client.getBankInstructions(payableId).then(
      (found) => live && setInstructions(found),
      (caught) => {
        if (!live) return;
        // A payment is already under way: show it instead of details for a second one.
        const open = openPaymentHref(caught);
        if (open) {
          router.replace(open);
          return;
        }
        setLoadError(describePaymentError(caught));
      },
    );
    return () => {
      live = false;
    };
  }, [client, payableId, router]);

  const rows = instructions
    ? [
        { label: "Bank", value: instructions.bankName },
        { label: "Account name", value: instructions.accountName },
        { label: "Account number", value: instructions.accountNumber },
        ...(instructions.branch ? [{ label: "Branch", value: instructions.branch }] : []),
        { label: "Reference", value: instructions.reference },
      ]
    : [];

  return (
    <Screen
      signedIn
      footer={
        instructions ? (
          <Button
            label="I have sent it, upload receipt"
            onPress={() => router.push(`/pay/bank/receipt/${instructions.payable.id}`)}
            testID="bank-sent"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Pay by bank transfer" }} />
      <BackButton fallback={`/pay/${payableId}`} />
      <View style={{ marginTop: 20, gap: 10 }}>
        <Title>Pay by bank transfer</Title>
        {instructions ? (
          <Txt color={colors.muted}>
            {`Send ${formatMoney(instructions.payable.amount)} to the account below, from your banking app or at a branch. It pays for your ${instructions.payable.title}.`}
          </Txt>
        ) : null}
      </View>

      {loadError ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">{loadError}</Notice>
        </View>
      ) : !instructions ? (
        <Loading label="Loading the bank details" />
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <Card padded={false}>
            {rows.map((row, index) => (
              <CopyRow key={row.label} label={row.label} value={row.value} first={index === 0} />
            ))}
          </Card>
          <Notice tone="neutral">
            {`Put ${instructions.reference} as the reference. It is your Finvest account number and lets us match your money without delay.`}
          </Notice>
          <Notice tone="warning">
            Send from a bank account in your own name. We refuse money that comes from an account in
            another name.
          </Notice>
          <Txt size={fontSizes.sm} color={colors.muted}>
            Your investment starts once we find the money on our bank statement. That is usually the
            next working day.
          </Txt>
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: 18,
  },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  copy: {
    minWidth: 72,
    height: 44,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 14,
  },
});
