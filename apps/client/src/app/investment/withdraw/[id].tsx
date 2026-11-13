import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatAmount } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { describeInvestingError, Investment, InvestingError, PayoutAccount } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { formatDateTime } from "@/payments/format";
import { newIdempotencyKey } from "@/payments/idempotency";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";

/**
 * Withdraw a running investment early. The client sees exactly what they will get, the
 * amount invested plus the interest earned after tax so far with no fee, chooses an approved
 * account, and confirms. Interest stops when they confirm. Nothing on this screen works a
 * figure out: it is the `value` the API sent.
 */
export default function Withdraw() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = useInvesting();
  const [loaded, retry] = useLoaded(
    useCallback(async (): Promise<{ investment: Investment; accounts: PayoutAccount[] }> => {
      const [investment, accounts] = await Promise.all([client.getInvestment(id), client.listPayoutAccounts()]);
      return { investment, accounts };
    }, [client, id]),
  );
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per request, as on the review screen. After a try whose outcome is unknown the
  // account is locked, so trying again asks for the same thing under the same key. After a
  // definite refusal nothing was asked, so the next try gets a new key.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [locked, setLocked] = useState(false);

  const accounts = loaded.state === "ready" ? loaded.value.accounts : [];
  const firstReady = accounts.find((account) => account.ready)?.id ?? null;
  const destinationId = chosen ?? firstReady;
  const canConfirm = loaded.state === "ready" && loaded.value.investment.canWithdraw && destinationId !== null;

  const confirm = async () => {
    if (!canConfirm || destinationId === null) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await client.withdrawEarly({ investmentId: id, destinationId, idempotencyKey });
      // Replace, so Back does not land on a confirm button for money already asked for.
      router.replace(`/investment/${id}`);
    } catch (caught) {
      const unknown = !(caught instanceof InvestingError) || caught.code === "unavailable";
      if (unknown) {
        setLocked(true);
        setError(`${describeInvestingError(caught)} Trying again sends the same request, so you will not withdraw twice.`);
      } else {
        setIdempotencyKey(newIdempotencyKey());
        setError(describeInvestingError(caught));
      }
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        canConfirm ? (
          <Button label="Withdraw now" onPress={confirm} loading={busy} testID="confirm-withdraw" />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Withdraw early" }} />
      <BackButton fallback={`/investment/${id}`} />
      <View style={{ marginTop: 20 }}>
        <Title>Withdraw early</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your investment" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : !loaded.value.investment.canWithdraw ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="warning">{describeInvestingError(new InvestingError("cannot_withdraw"))}</Notice>
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
              You get
            </Txt>
            <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }} testID="withdraw-amount">
              {formatAmount(loaded.value.investment.value)}
            </Txt>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
              No fee. Interest stops when you confirm.
            </Txt>
          </View>
          <DetailList
            rows={[
              { label: "Investment", value: loaded.value.investment.productName },
              { label: "You invested", value: formatAmount(loaded.value.investment.amount) },
              { label: "Interest so far, after tax", value: formatAmount(loaded.value.investment.interestSoFar) },
              ...(loaded.value.investment.interestAtMaturity
                ? [
                    {
                      label: "Interest if kept to maturity",
                      value: formatAmount(loaded.value.investment.interestAtMaturity),
                    },
                  ]
                : []),
            ]}
          />
          <View style={{ gap: 8 }} accessibilityRole="radiogroup">
            <Eyebrow>Pay it to</Eyebrow>
            {accounts.length === 0 ? (
              <Notice tone="warning">
                You need an approved account to be paid to. Ask us in Help to add one, then come back.
              </Notice>
            ) : (
              accounts.map((account) => {
                const isReady = account.ready;
                const selected = isReady && account.id === destinationId;
                const disabled = locked || !isReady;
                return (
                  <Pressable
                    key={account.id}
                    accessibilityRole="radio"
                    accessibilityState={{ checked: selected, disabled }}
                    accessibilityLabel={account.label}
                    disabled={disabled}
                    onPress={() => setChosen(account.id)}
                    testID={`account-${account.id}`}
                    style={({ pressed }) => [
                      styles.option,
                      selected && styles.selected,
                      !isReady && styles.waiting,
                      pressed && { opacity: 0.85 },
                    ]}
                  >
                    <View style={[styles.radio, selected && styles.radioOn]} />
                    <View style={{ flex: 1, gap: 2 }}>
                      <Txt size={fontSizes.md} weight="bold">
                        {account.label}
                      </Txt>
                      <Txt size={fontSizes.sm} color={colors.muted}>
                        {isReady
                          ? account.kind === "mobile_money"
                            ? "Mobile money"
                            : "Bank account"
                          : account.payableFrom
                            ? `New account. It can be paid from ${formatDateTime(account.payableFrom)}.`
                            : "New account. It can be paid one day after it is approved."}
                      </Txt>
                    </View>
                  </Pressable>
                );
              })
            )}
            {locked ? (
              <Txt size={fontSizes.xs} color={colors.muted}>
                Your last try may have gone through, so the account stays as it was.
              </Txt>
            ) : null}
          </View>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          <Txt size={fontSizes.xs} color={colors.muted}>
            Once you confirm, this cannot be undone. We will tell you when the money is sent.
          </Txt>
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 16,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  selected: { borderColor: colors.black, borderWidth: 2, padding: 15 },
  waiting: { opacity: 0.6 },
  radio: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, borderColor: colors.borderStrong },
  radioOn: { borderColor: colors.black, borderWidth: 6 },
});
