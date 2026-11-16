import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useBonds } from "@/bonds/BondsProvider";
import { heldLabel } from "@/bonds/format";
import { Amount, BondsError, describeBondsError, HeldKind } from "@/bonds/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { usePinConfirmation } from "@/components/pin/ConfirmWithPin";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { AccountPicker, firstChoosable, NoAccount } from "@/investing/AccountPicker";
import { formatAmount, formatDay } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { PayoutAccount } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

type Waiting = { title: string; line: string; amount: Amount; heldBecause: string | null; holdingId: string };

const KINDS: Record<HeldKind, string> = { coupon: "A coupon", repayment: "Repaid at maturity", sale: "Sold early" };

/**
 * Send on money from a bond that waits for an account: a coupon, a repayment at maturity, or a
 * sale's proceeds. The client picks an approved account past its first day and confirms with
 * their PIN. Sending the same one twice is refused by the server, so this needs no key.
 */
export default function BondPayOut() {
  const router = useRouter();
  const params = useLocalSearchParams<{ kind: string; id: string }>();
  const kind: HeldKind | null = params.kind === "coupon" || params.kind === "repayment" || params.kind === "sale" ? params.kind : null;
  const client = useBonds();
  const investing = useInvesting();
  const [loaded, retry] = useLoaded(
    useCallback(async (): Promise<{ waiting: Waiting; accounts: PayoutAccount[] }> => {
      const accounts = await investing.listPayoutAccounts();
      const waiting = await findWaiting();
      return { waiting, accounts };

      async function findWaiting(): Promise<Waiting> {
        if (kind === "coupon") {
          const coupon = (await client.listCoupons()).find((item) => item.id === params.id && item.status === "held");
          if (coupon) return { title: coupon.name, line: `Coupon of ${formatDay(coupon.couponDate)}`, amount: coupon.amount, heldBecause: coupon.heldBecause, holdingId: coupon.holdingId };
        } else if (kind === "repayment") {
          const item = (await client.listRepayments()).find((found) => found.id === params.id && found.status === "held");
          if (item) return { title: item.name, line: `Face value and last coupon, ${formatDay(item.repaidOn)}`, amount: item.amount, heldBecause: item.heldBecause, holdingId: item.holdingId };
        } else if (kind === "sale") {
          const sale = (await client.listSales()).find((found) => found.id === params.id && found.payoutStatus === "held");
          if (sale?.amount) return { title: sale.name, line: sale.soldOn ? `Sold ${formatDay(sale.soldOn)}` : "Sold", amount: sale.amount, heldBecause: sale.heldBecause, holdingId: sale.holdingId };
        }
        throw new BondsError("already_sent");
      }
    }, [client, investing, kind, params.id]),
    describeBondsError,
  );
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { ask: askPin, screen: pinScreen } = usePinConfirmation();

  const accounts = loaded.state === "ready" ? loaded.value.accounts : [];
  const destinationId = chosen ?? firstChoosable(accounts);
  const canConfirm = loaded.state === "ready" && kind !== null && destinationId !== null;

  const confirm = async () => {
    if (!canConfirm || kind === null || destinationId === null || loaded.state !== "ready") return;
    setBusy(true);
    setError(null);
    const answer = await askPin("withdrawal");
    if (answer === "cancelled") {
      setBusy(false);
      return;
    }
    try {
      await client.payOutHeld({ kind, id: params.id, destinationId, pinConfirmation: answer.confirmation });
      router.replace(kind === "coupon" ? `/investment/bond/${loaded.value.waiting.holdingId}` : "/portfolio");
    } catch (caught) {
      setError(describeBondsError(caught));
      setBusy(false);
    }
  };

  if (pinScreen) return pinScreen;

  return (
    <Screen
      signedIn
      footer={canConfirm ? <Button label="Send it now" onPress={() => void confirm()} loading={busy} testID="confirm-bond-pay-out" /> : null}
    >
      <Stack.Screen options={{ title: "Send your money" }} />
      <BackButton fallback="/portfolio" />
      <View style={{ marginTop: 20 }}>
        <Title>Send your money</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading what is waiting" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
              {`${kind ? KINDS[kind] : ""} · ${loaded.value.waiting.title}`}
            </Txt>
            <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }} testID="bond-pay-out-amount">
              {formatAmount(loaded.value.waiting.amount)}
            </Txt>
            <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
              {`${loaded.value.waiting.line}. After tax.`}
            </Txt>
          </View>
          <Notice tone="warning">{heldLabel(loaded.value.waiting.heldBecause)}</Notice>
          <View style={{ gap: 8 }}>
            <Eyebrow>Pay it to</Eyebrow>
            {accounts.length === 0 ? (
              <NoAccount />
            ) : (
              <AccountPicker accounts={accounts} chosen={destinationId} onChoose={setChosen} />
            )}
          </View>
          {error ? <Notice tone="danger">{error}</Notice> : null}
        </View>
      )}
    </Screen>
  );
}
