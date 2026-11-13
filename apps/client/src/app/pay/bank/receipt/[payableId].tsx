import { useState } from "react";
import { Platform, Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { formatFileSize } from "@/payments/format";
import { newIdempotencyKey } from "@/payments/idempotency";
import { chooseReceiptFile, photographReceipt } from "@/payments/pickReceipt";
import {
  checkReceipt,
  groupDigits,
  parseDaySent,
  parseShillings,
  PickedFile,
  receiptProblemCopy,
} from "@/payments/receipt";
import { usePayments } from "@/payments/PaymentsProvider";
import { describePaymentError, openPaymentHref, PaymentError, ReceiptFile } from "@/payments/types";
import { colors, fontSizes, radii } from "@/theme";

/**
 * Pay by bank transfer, step 2: the receipt, and what the client says they
 * sent. Nothing here is read from the receipt by the app. Staff compare what
 * is typed with the receipt and with the bank statement.
 */
export default function UploadReceipt() {
  const router = useRouter();
  const { payableId } = useLocalSearchParams<{ payableId: string }>();
  const { client } = usePayments();

  const [file, setFile] = useState<ReceiptFile | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [daySent, setDaySent] = useState("");
  const [bankName, setBankName] = useState("");
  const [transactionNumber, setTransactionNumber] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set once a try may have been saved. From then on the same receipt and details are sent.
  const [locked, setLocked] = useState(false);
  const [idempotencyKey] = useState(newIdempotencyKey);
  // "Not in the future" is judged against the moment the screen was opened.
  const [openedAt] = useState(() => Date.now());

  const take = (picked: PickedFile | null) => {
    if (!picked) {
      return;
    }
    const checked = checkReceipt(picked);
    if (typeof checked === "string") {
      setFile(null);
      setFileError(receiptProblemCopy[checked]);
    } else {
      setFile(checked);
      setFileError(null);
    }
  };

  const choose = async () => {
    try {
      take(await chooseReceiptFile());
    } catch {
      setFileError("We could not open that file. Try another one.");
    }
  };

  const photograph = async () => {
    try {
      const result = await photographReceipt();
      if (result === "no_permission") {
        setFileError("Finvest is not allowed to use the camera. Choose a file instead.");
        return;
      }
      take(result);
    } catch {
      setFileError("We could not open the camera. Choose a file instead.");
    }
  };

  const shillings = parseShillings(amount);
  const sentOn = parseDaySent(daySent, openedAt);
  const bank = bankName.trim();
  const number = transactionNumber.trim();
  const problems = {
    file: !file ? (fileError ?? "Add your receipt.") : null,
    amount: shillings === null ? "Enter the amount you sent, in shillings." : null,
    day:
      sentOn === null
        ? "Enter the day you sent it, like 13/11/2026. It cannot be in the future."
        : null,
    bank: bank.length < 2 ? "Enter the bank you sent from." : null,
    number: number.length < 4 ? "Enter the transaction number on the receipt." : null,
  };

  const submit = async () => {
    setSubmitted(true);
    setError(null);
    if (!file || shillings === null || sentOn === null || problems.bank || problems.number) {
      return;
    }
    setBusy(true);
    try {
      const transfer = await client.submitBankReceipt({
        payableId,
        file,
        amountSent: { currency: "UGX", minor: shillings },
        sentOn,
        bankName: bank,
        transactionNumber: number,
        idempotencyKey,
      });
      router.replace(`/pay/bank/transfer/${transfer.id}`);
    } catch (caught) {
      // Already paying: show that payment instead of a form that cannot pay again.
      const open = openPaymentHref(caught);
      if (open) {
        router.replace(open);
        return;
      }
      if (!(caught instanceof PaymentError) || caught.code === "unavailable") {
        setLocked(true);
      }
      setError(describePaymentError(caught));
      setBusy(false);
    }
  };

  const show = (problem: string | null) => (submitted ? problem : null);

  return (
    <Screen
      signedIn
      footer={<Button label="Submit receipt" onPress={submit} loading={busy} testID="receipt-submit" />}
    >
      <Stack.Screen options={{ title: "Upload your receipt" }} />
      <BackButton fallback={`/pay/bank/${payableId}`} />
      <View style={{ marginTop: 20, gap: 10 }}>
        <Title>Upload your receipt</Title>
        <Txt color={colors.muted}>
          A bank slip, a screenshot from your banking app or the PDF your bank emailed. JPG, PNG or
          PDF up to 10 MB.
        </Txt>
      </View>

      <View style={{ marginTop: 20, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {locked ? (
          <Notice tone="warning">
            Your first try may already be with us, so the receipt and details are fixed. Pressing
            Submit again sends the same thing. It does not create a second payment.
          </Notice>
        ) : null}

        {file ? (
          <View style={styles.file} testID="receipt-file">
            <View style={styles.fileBadge}>
              <Txt size={fontSizes.xs} weight="extrabold">
                {file.mimeType === "application/pdf" ? "PDF" : "IMG"}
              </Txt>
            </View>
            <View style={{ flex: 1 }}>
              <Txt weight="bold" numberOfLines={1}>
                {file.name}
              </Txt>
              <Txt size={fontSizes.xs} color={colors.muted}>
                {`${formatFileSize(file.sizeBytes)}, ready to send`}
              </Txt>
            </View>
          </View>
        ) : null}
        {locked ? null : (
          <View style={styles.pickers}>
            {Platform.OS === "web" ? null : (
              <Picker label="Take a photo" onPress={photograph} testID="receipt-photo" />
            )}
            <Picker
              label={file ? "Choose another file" : "Choose a file"}
              onPress={choose}
              testID="receipt-choose"
            />
          </View>
        )}
        {fileError || show(problems.file) ? (
          <Txt size={fontSizes.xs} weight="semibold" color={colors.danger} accessibilityLiveRegion="polite">
            {fileError ?? problems.file}
          </Txt>
        ) : null}

        <Txt weight="bold" style={{ marginTop: 6 }}>
          What you sent
        </Txt>
        <TextField
          label="Amount sent, UGX"
          value={amount}
          onChangeText={(text) => setAmount(groupDigits(text))}
          keyboardType="number-pad"
          inputMode="numeric"
          placeholder="2,000,000"
          error={show(problems.amount)}
          editable={!locked}
          testID="receipt-amount"
        />
        <TextField
          label="Date sent"
          value={daySent}
          onChangeText={setDaySent}
          placeholder="13/11/2026"
          keyboardType="numbers-and-punctuation"
          hint="Day, month, year."
          error={show(problems.day)}
          editable={!locked}
          testID="receipt-day"
        />
        <TextField
          label="Bank you sent from"
          value={bankName}
          onChangeText={setBankName}
          autoCapitalize="words"
          maxLength={80}
          error={show(problems.bank)}
          editable={!locked}
          testID="receipt-bank"
        />
        <TextField
          label="Transaction number on the receipt"
          value={transactionNumber}
          onChangeText={setTransactionNumber}
          autoCapitalize="characters"
          autoCorrect={false}
          maxLength={64}
          error={show(problems.number)}
          editable={!locked}
          testID="receipt-number"
        />
      </View>
    </Screen>
  );
}

function Picker({ label, onPress, testID }: { label: string; onPress: () => void; testID: string }) {
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [styles.picker, pressed && { opacity: 0.7 }]}
    >
      <Txt weight="bold">{label}</Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pickers: { flexDirection: "row", gap: 10 },
  picker: {
    flex: 1,
    height: 54,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  file: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 14,
    borderRadius: radii.lg,
    backgroundColor: colors.successSurface,
  },
  fileBadge: {
    width: 44,
    height: 44,
    borderRadius: radii.sm,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
});
