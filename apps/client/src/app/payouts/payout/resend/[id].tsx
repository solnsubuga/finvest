import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { LoadFailed } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { AccountPicker, firstChoosable, NoAccount } from "@/investing/AccountPicker";
import type { PayoutAccount as Choice } from "@/investing/types";
import { formatDateTime, formatMoney } from "@/payments/format";
import { Loading } from "@/payments/Parts";
import { accountLabel, isPayable } from "@/payouts/format";
import { useFocusLoaded } from "@/payouts/Parts";
import { usePayouts } from "@/payouts/PayoutsProvider";
import { describePayoutsError, Payout, PayoutsError } from "@/payouts/types";
import { colors, fontSizes } from "@/theme";

type Read = { payout: Payout; choices: Choice[] };

/**
 * Send a payout that came back again, to an approved account. The amount is the payout's own:
 * nothing is worked out here. Asking again with the same account is the same payout, so a lost
 * answer is safe to try again as it is; the account is then locked so the request cannot change.
 */
export default function ResendPayout() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = usePayouts();
  const [loaded, retry] = useFocusLoaded(
    useCallback(async (): Promise<Read> => {
      const [payout, accounts] = await Promise.all([client.getPayout(id), client.listAccounts()]);
      const nowMs = Date.now();
      // Only approved accounts are offered. One still in its first day shows, but cannot be chosen.
      const choices = accounts
        .filter((account) => account.status === "approved")
        .map((account) => ({
          id: account.id,
          kind: account.kind,
          label: accountLabel(account),
          payableFrom: account.payableFrom,
          ready: isPayable(account, nowMs),
        }));
      return { payout, choices };
    }, [client, id]),
  );
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);

  const read = loaded.state === "ready" ? loaded.value : null;
  const returned = read !== null && read.payout.returnedAt !== null;
  const destinationId = chosen ?? (read ? firstChoosable(read.choices) : null);
  const canSend = returned && destinationId !== null;

  const send = async () => {
    if (!canSend || destinationId === null) return;
    setBusy(true);
    setError(null);
    try {
      const sent = await client.resend(id, destinationId);
      router.replace(`/payouts/payout/${sent.id}`);
    } catch (caught) {
      const unknown = !(caught instanceof PayoutsError) || caught.code === "unavailable";
      setLocked(unknown);
      setError(
        unknown
          ? `${describePayoutsError(caught)} Trying again asks for the same payout, so it will not be sent twice.`
          : describePayoutsError(caught),
      );
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={canSend ? <Button label="Send it again" onPress={send} loading={busy} testID="resend-send" /> : null}
    >
      <Stack.Screen options={{ title: "Send it again" }} />
      <BackButton fallback={`/payouts/payout/${id}`} />
      <View style={{ marginTop: 20 }}>
        <Title>Send it again</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your payout" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <LoadFailed message={loaded.message} onRetry={retry} />
        </View>
      ) : !returned ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="neutral">{describePayoutsError(new PayoutsError("not_returned"))}</Notice>
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
              Waiting for you
            </Txt>
            <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }} testID="resend-amount">
              {formatMoney(loaded.value.payout.amount)}
            </Txt>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
              {`Came back on ${formatDateTime(loaded.value.payout.returnedAt ?? "")}`}
            </Txt>
          </View>
          <View style={{ gap: 8 }}>
            <Eyebrow>Send it to</Eyebrow>
            {loaded.value.choices.length === 0 ? (
              <NoAccount />
            ) : (
              <AccountPicker accounts={loaded.value.choices} chosen={destinationId} onChoose={setChosen} locked={locked} />
            )}
          </View>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          <Txt size={fontSizes.xs} color={colors.muted}>
            It can be sent again once. If you already chose an account, choosing the same one shows that payout. We will
            tell you when it arrives.
          </Txt>
        </View>
      )}
    </Screen>
  );
}
