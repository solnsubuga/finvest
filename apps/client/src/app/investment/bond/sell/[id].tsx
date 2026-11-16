import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useBonds } from "@/bonds/BondsProvider";
import { formatPrice } from "@/bonds/format";
import { BondHolding, BondsError, describeBondsError } from "@/bonds/types";
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
import { newIdempotencyKey } from "@/payments/idempotency";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/**
 * Ask Finvest to sell a whole bond holding before it matures. The dealing desk sells it on the
 * market, so the price is not known yet and can be below what the client paid. The client names
 * the account the money goes to and confirms with their PIN; nothing moves until it is sold.
 */
export default function SellBond() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = useBonds();
  const investing = useInvesting();
  const [loaded, retry] = useLoaded(
    useCallback(async (): Promise<{ holding: BondHolding; accounts: PayoutAccount[] }> => {
      const [holding, accounts] = await Promise.all([client.getHolding(id), investing.listPayoutAccounts()]);
      return { holding, accounts };
    }, [client, investing, id]),
    describeBondsError,
  );
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // As on the withdraw screen: after a try whose outcome is unknown the account is locked and
  // the same key is sent again; after a definite refusal the next try gets a new key.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [locked, setLocked] = useState(false);
  const { ask: askPin, screen: pinScreen } = usePinConfirmation();

  const accounts = loaded.state === "ready" ? loaded.value.accounts : [];
  const destinationId = chosen ?? firstChoosable(accounts);
  const active = loaded.state === "ready" && loaded.value.holding.status === "active";
  const canConfirm = active && destinationId !== null;

  const confirm = async () => {
    if (!canConfirm || destinationId === null) return;
    setBusy(true);
    setError(null);
    const answer = await askPin("withdrawal");
    if (answer === "cancelled") {
      setBusy(false);
      return;
    }
    try {
      await client.sell({ holdingId: id, destinationId, idempotencyKey, pinConfirmation: answer.confirmation });
      // Replace, so Back does not land on a confirm button for a sale already asked for.
      router.replace(`/investment/bond/${id}`);
    } catch (caught) {
      const unknown = !(caught instanceof BondsError) || caught.code === "unavailable";
      if (unknown) {
        setLocked(true);
        setError(`${describeBondsError(caught)} Trying again sends the same request, so you will not ask twice.`);
      } else {
        setIdempotencyKey(newIdempotencyKey());
        setError(describeBondsError(caught));
      }
      setBusy(false);
    }
  };

  if (pinScreen) return pinScreen;

  return (
    <Screen
      signedIn
      footer={
        canConfirm ? <Button label="Ask to sell" onPress={() => void confirm()} loading={busy} testID="confirm-sell" /> : null
      }
    >
      <Stack.Screen options={{ title: "Sell early" }} />
      <BackButton fallback={`/investment/bond/${id}`} />
      <View style={{ marginTop: 20 }}>
        <Title>Sell early</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your bond" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : !active ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="warning">{describeBondsError(new BondsError("cannot_sell"))}</Notice>
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <Notice tone="neutral" title="Sold at the market price">
            {
              "Finvest's dealing desk sells your whole holding to another buyer at the price the market pays that day, which can be below what you paid. You are paid that price and the interest earned since the last coupon, after tax. Coupons are still paid to you until it is sold, and you can cancel until a price is found."
            }
          </Notice>
          <DetailList
            rows={[
              { label: "Bond", value: loaded.value.holding.name },
              { label: "Face value", value: formatAmount(loaded.value.holding.face) },
              { label: "You paid", value: `${formatAmount(loaded.value.holding.cost)}, at ${formatPrice(loaded.value.holding.price)} per 100` },
              { label: "If you keep it", value: `${formatAmount(loaded.value.holding.stillToCome)} by ${formatDay(loaded.value.holding.maturityDate)}` },
            ]}
          />
          <View style={{ gap: 8 }}>
            <Eyebrow>Pay the money to</Eyebrow>
            {accounts.length === 0 ? (
              <NoAccount />
            ) : (
              <AccountPicker accounts={accounts} chosen={destinationId} onChoose={setChosen} locked={locked} />
            )}
          </View>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          <Txt size={fontSizes.xs} color={colors.muted}>
            Selling usually takes a few working days. We show the price here once the desk has sold it.
          </Txt>
        </View>
      )}
    </Screen>
  );
}
