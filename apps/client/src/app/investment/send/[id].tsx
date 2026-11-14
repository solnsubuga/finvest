import { useCallback, useState } from "react";
import { Pressable, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Field, Input, TextField } from "@/components/Field";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatAmount, formatDay, groupDigits, parseWholeAmount } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { Amount, describeInvestingError, Investment, InvestingError, Recipient } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { newIdempotencyKey } from "@/payments/idempotency";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";

const MAX_MESSAGE = 140;

/**
 * Send part or all of a running investment to another Finvest client. The client finds the
 * recipient by their account number or mobile number and sees them as "Brian K.", types an
 * amount or sends everything, and confirms. Nothing is cashed out: the recipient gets a new
 * investment on the same rate and maturity date. The server judges every figure again.
 */
export default function Send() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = useInvesting();
  const [loaded, retry] = useLoaded(useCallback(() => client.getInvestment(id), [client, id]));

  const [to, setTo] = useState("");
  const [recipient, setRecipient] = useState<Recipient | null>(null);
  const [finding, setFinding] = useState(false);
  const [findError, setFindError] = useState<string | null>(null);
  const [amountText, setAmountText] = useState("");
  const [everything, setEverything] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per request. After a try whose outcome is unknown the details are locked, so
  // trying again asks for the same thing under the same key and cannot send twice.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [locked, setLocked] = useState(false);

  const investment = loaded.state === "ready" ? loaded.value : null;
  const amount: Amount | null = investment
    ? everything
      ? investment.value
      : parseWholeAmount(amountText, investment.value.currency)
    : null;
  const canConfirm = Boolean(investment?.canSend && recipient && amount);

  const find = async () => {
    if (!to.trim()) return;
    setFinding(true);
    setFindError(null);
    try {
      setRecipient(await client.findRecipient(to.trim()));
    } catch (caught) {
      setRecipient(null);
      setFindError(describeInvestingError(caught));
    }
    setFinding(false);
  };

  const confirm = async () => {
    if (!investment || !recipient || !amount) return;
    setBusy(true);
    setError(null);
    try {
      const transfer = await client.sendInvestment({
        investmentId: investment.id,
        toAccountNumber: recipient.accountNumber,
        amount,
        message: message.trim() || null,
        idempotencyKey,
      });
      // Replace, so Back does not land on a send button for money already sent.
      router.replace(`/transfers/${transfer.id}`);
    } catch (caught) {
      const unknown = !(caught instanceof InvestingError) || caught.code === "unavailable";
      if (unknown) {
        setLocked(true);
        setError(`${describeInvestingError(caught)} Trying again sends the same request, so you will not send twice.`);
      } else {
        setIdempotencyKey(newIdempotencyKey());
        setError(describeInvestingError(caught));
      }
      setBusy(false);
    }
  };

  const first = recipient?.name.split(" ")[0];
  return (
    <Screen
      signedIn
      footer={
        investment?.canSend && recipient ? (
          <Button
            label={`Send to ${first}`}
            onPress={confirm}
            loading={busy}
            disabled={!canConfirm}
            testID="confirm-send"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Send to someone" }} />
      <BackButton fallback={`/investment/${id}`} />
      <View style={{ marginTop: 20 }}>
        <Title>Send to someone</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your investment" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : !loaded.value.canSend ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="warning">{describeInvestingError(new InvestingError("cannot_send"))}</Notice>
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 18 }}>
          <Field
            label="To"
            hint="Their Finvest account number or mobile number"
            error={findError}
          >
            <Input
              label="To"
              value={to}
              onChangeText={(typed) => {
                setTo(typed);
                if (!locked) setRecipient(null);
                setFindError(null);
              }}
              editable={!locked}
              onSubmitEditing={find}
              placeholder="1001005170 or 0772 123 456"
              keyboardType="phone-pad"
              invalid={Boolean(findError)}
              testID="send-to"
            />
          </Field>
          {recipient ? (
            <RecipientCard recipient={recipient} />
          ) : (
            <Button label="Find" variant="secondary" onPress={find} loading={finding} disabled={!to.trim()} testID="find-recipient" />
          )}
          {recipient ? (
            <>
              <FromCard investment={loaded.value} />
              <Field
                label="Amount"
                hint={everything ? "Everything, including interest added before it is sent" : `Up to ${formatAmount(loaded.value.value)}`}
                error={!everything && amountText && !amount ? "Type a whole amount, such as 1,000,000." : null}
              >
                <Input
                  label="Amount"
                  value={everything ? groupDigits(String(loaded.value.value.minor)) : amountText}
                  onChangeText={(typed) => setAmountText(groupDigits(typed))}
                  editable={!everything && !locked}
                  keyboardType="number-pad"
                  placeholder="1,000,000"
                  testID="send-amount"
                />
              </Field>
              <Pressable
                accessibilityRole="checkbox"
                accessibilityState={{ checked: everything, disabled: locked }}
                disabled={locked}
                onPress={() => setEverything((value) => !value)}
                style={{ flexDirection: "row", alignItems: "center", gap: 10 }}
                testID="send-everything"
              >
                <View
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: 6,
                    borderWidth: 2,
                    borderColor: colors.black,
                    backgroundColor: everything ? colors.black : colors.white,
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {everything ? <Icon name="check" size={14} color={colors.white} strokeWidth={3} /> : null}
                </View>
                <Txt weight="semibold">Send everything</Txt>
              </Pressable>
              <Notice tone="neutral">
                {`${first} keeps earning ${loaded.value.ratePercent}% a year after tax until this ${loaded.value.productName} matures${
                  loaded.value.maturityDate ? ` on ${formatDay(loaded.value.maturityDate)}` : ""
                }. Nothing is cashed out. ${everything ? "" : "You keep the rest, on the same terms."}`.trim()}
              </Notice>
              <TextField
                label="Message (optional)"
                value={message}
                onChangeText={(typed) => setMessage(typed.slice(0, MAX_MESSAGE))}
                editable={!locked}
                placeholder="For school fees"
                hint={`${MAX_MESSAGE - message.length} characters left`}
                testID="send-message"
              />
              {error ? <Notice tone="danger">{error}</Notice> : null}
              <Txt size={fontSizes.xs} color={colors.muted}>
                Once it is sent, it cannot be undone. Large amounts are checked by Finvest first, and you can cancel until then.
              </Txt>
            </>
          ) : null}
        </View>
      )}
    </Screen>
  );
}

function RecipientCard({ recipient }: { recipient: Recipient }) {
  return (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        padding: 14,
        borderRadius: radii.lg,
        backgroundColor: colors.white,
        borderWidth: 1,
        borderColor: colors.border,
      }}
      testID="recipient"
    >
      <View
        style={{
          width: 42,
          height: 42,
          borderRadius: 21,
          backgroundColor: colors.lime,
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Txt weight="extrabold">{recipient.name.slice(0, 1)}</Txt>
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <Txt weight="bold">{recipient.name}</Txt>
        <Txt size={fontSizes.xs} color={colors.muted}>
          {`Finvest account ${recipient.accountNumber}`}
        </Txt>
      </View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
        <Icon name="check" size={14} color={colors.success} strokeWidth={3} />
        <Txt size={fontSizes.xs} weight="bold" color={colors.success}>
          Verified
        </Txt>
      </View>
    </View>
  );
}

function FromCard({ investment }: { investment: Investment }) {
  return (
    <View style={{ gap: 8 }}>
      <Eyebrow>From</Eyebrow>
      <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 18, gap: 4 }}>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
          {investment.productName}
        </Txt>
        <Txt size={28} weight="extrabold" lineHeight={34} color={colors.white} style={{ letterSpacing: -0.6 }} testID="send-from-value">
          {formatAmount(investment.value)}
        </Txt>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
          {`${investment.ratePercent}% a year after tax${investment.maturityDate ? `, matures ${formatDay(investment.maturityDate)}` : ""}`}
        </Txt>
      </View>
    </View>
  );
}
