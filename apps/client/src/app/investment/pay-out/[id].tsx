import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { AccountPicker, firstChoosable, NoAccount } from "@/investing/AccountPicker";
import { formatAmount } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { describeInvestingError, Investment, InvestingError, PayoutAccount } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/**
 * Send the money of a matured investment that has no account to go to. Asking twice for the
 * same account is the same payout, so a lost answer is safe to try again as it is; another
 * account after a refusal is a new request.
 */
export default function PayOutMatured() {
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
  // After an unknown outcome the account stays put, so trying again asks for the same payout.
  const [locked, setLocked] = useState(false);

  const accounts = loaded.state === "ready" ? loaded.value.accounts : [];
  const destinationId = chosen ?? firstChoosable(accounts);
  const waiting = loaded.state === "ready" && loaded.value.investment.waitingForDestination;
  const canSend = waiting && destinationId !== null;

  const send = async () => {
    if (!canSend || destinationId === null) return;
    setBusy(true);
    setError(null);
    try {
      await client.payOutMatured({ investmentId: id, destinationId });
      router.replace(`/investment/${id}`);
    } catch (caught) {
      const unknown = !(caught instanceof InvestingError) || caught.code === "unavailable";
      setLocked(unknown);
      setError(
        unknown
          ? `${describeInvestingError(caught)} Trying again asks for the same payout, so you will not be paid twice.`
          : describeInvestingError(caught),
      );
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={canSend ? <Button label="Send it to me" onPress={send} loading={busy} testID="pay-out-send" /> : null}
    >
      <Stack.Screen options={{ title: "Choose where to be paid" }} />
      <BackButton fallback={`/investment/${id}`} />
      <View style={{ marginTop: 20 }}>
        <Title>Choose where to be paid</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your investment" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : !waiting ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="neutral">{describeInvestingError(new InvestingError("already_sent"))}</Notice>
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
              Ready for you
            </Txt>
            <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }} testID="pay-out-amount">
              {formatAmount(loaded.value.investment.value)}
            </Txt>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
              {`${loaded.value.investment.productName} has matured. No fee.`}
            </Txt>
          </View>
          <View style={{ gap: 8 }}>
            <Eyebrow>Pay it to</Eyebrow>
            {accounts.length === 0 ? (
              <NoAccount />
            ) : (
              <AccountPicker accounts={accounts} chosen={destinationId} onChoose={setChosen} locked={locked} />
            )}
          </View>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          <Txt size={fontSizes.xs} color={colors.muted}>
            Your money is safe with us until you choose. We will tell you when it is sent.
          </Txt>
        </View>
      )}
    </Screen>
  );
}
