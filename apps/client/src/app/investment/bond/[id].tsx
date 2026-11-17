import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useBonds } from "@/bonds/BondsProvider";
import { formatPrice, heldLabel, holdingStatusLabel, saleStatusLabel } from "@/bonds/format";
import { openSaleOf, payOutHref } from "@/bonds/Parts";
import { BondCoupon, BondHolding, BondSale, describeBondsError } from "@/bonds/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { AccountPicker, NoAccount } from "@/investing/AccountPicker";
import { formatAmount, formatDay } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { PayoutAccount } from "@/investing/types";
import { useFocusLoaded } from "@/investing/useLoaded";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";

type Loaded = { holding: BondHolding; coupons: BondCoupon[]; sales: BondSale[]; accounts: PayoutAccount[] };

/**
 * One bond holding: what was bought, what is still to come, where its coupons are paid, every
 * coupon so far, and selling it early. Every figure is the backend's, after tax.
 */
export default function BondHoldingDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = useBonds();
  const investing = useInvesting();
  const [loaded, retry] = useFocusLoaded(
    useCallback(async (): Promise<Loaded> => {
      const [holding, coupons, sales, accounts] = await Promise.all([
        client.getHolding(id),
        client.listCoupons(),
        client.listSales(),
        investing.listPayoutAccounts(),
      ]);
      return {
        holding,
        coupons: coupons.filter((coupon) => coupon.holdingId === id),
        sales: sales.filter((sale) => sale.holdingId === id),
        accounts,
      };
    }, [client, investing, id]),
    describeBondsError,
  );
  // The holding as last saved here, once the client chose where its coupons go.
  const [saved, setSaved] = useState<BondHolding | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  if (loaded.state === "loading") {
    return (
      <Screen signedIn>
        <BackButton fallback="/portfolio" />
        <Loading label="Loading your bond" />
      </Screen>
    );
  }
  if (loaded.state === "failed") {
    return (
      <Screen signedIn>
        <BackButton fallback="/portfolio" />
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      </Screen>
    );
  }

  const holding = saved?.id === id ? saved : loaded.value.holding;
  const { coupons, sales, accounts } = loaded.value;
  const open = openSaleOf(sales, id);
  const sold = sales.find((sale) => sale.status === "sold");
  const active = holding.status === "active";
  const finished = holding.status === "sold" || holding.status === "matured";

  const choose = async (destinationId: string) => {
    if (destinationId === holding.couponDestinationId) return;
    setSaving(true);
    setError(null);
    try {
      setSaved(await client.chooseCouponDestination(id, destinationId));
    } catch (caught) {
      setError(describeBondsError(caught));
    }
    setSaving(false);
  };

  const cancel = async (sale: BondSale) => {
    setCancelling(true);
    setError(null);
    try {
      await client.cancelSale(sale.id);
      retry();
    } catch (caught) {
      setError(describeBondsError(caught));
    }
    setCancelling(false);
  };

  return (
    <Screen
      signedIn
      footer={
        active && !open ? (
          <Button
            label="Sell early"
            variant="secondary"
            onPress={() => router.push(`/investment/bond/sell/${id}`)}
            testID="bond-sell"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: holding.name }} />
      <BackButton fallback="/portfolio" />
      <View style={{ marginTop: 20, gap: 18 }}>
        <View style={{ gap: 6 }}>
          <Eyebrow>{holdingStatusLabel[holding.status]}</Eyebrow>
          <Title>{holding.name}</Title>
        </View>
        <View style={styles.black}>
          <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
            Face value
          </Txt>
          <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }}>
            {formatAmount(holding.face)}
          </Txt>
          <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
            {active && holding.nextCouponDate
              ? `${formatAmount(holding.coupon)} coupon after tax on ${formatDay(holding.nextCouponDate)}`
              : holding.status === "sold"
                ? `Sold${sold?.soldOn ? ` on ${formatDay(sold.soldOn)}` : ""}`
                : holding.status === "matured"
                  ? `Repaid on ${formatDay(holding.maturityDate)}`
                  : `Matures ${formatDay(holding.maturityDate)}`}
          </Txt>
        </View>
        <DetailList
          rows={[
            { label: "Bought at", value: `${formatPrice(holding.price)} per 100` },
            { label: "You paid", value: formatAmount(holding.cost) },
            ...(holding.startDate ? [{ label: "Started", value: formatDay(holding.startDate) }] : []),
            { label: "Matures", value: formatDay(holding.maturityDate) },
            { label: "Each coupon after tax", value: formatAmount(holding.coupon) },
            // A sold or repaid holding is paid nothing more.
            ...(finished
              ? []
              : [
                  { label: "Coupons left", value: String(holding.couponsLeft) },
                  { label: "Still to come", value: formatAmount(holding.stillToCome) },
                ]),
          ]}
        />

        {sold ? (
          <Notice tone="neutral" title={`Sold for ${sold.amount ? formatAmount(sold.amount) : "its market price"}`}>
            {sold.payoutStatus === "held"
              ? heldLabel(sold.heldBecause)
              : `Sold on ${sold.soldOn ? formatDay(sold.soldOn) : "the day it settled"}, and paid to your account.`}
          </Notice>
        ) : open ? (
          <View style={{ gap: 10 }} testID="bond-sale">
            <Notice tone="neutral" title="Selling early">
              {`${saleStatusLabel[open.status]} Asked on ${formatDay(open.requestedAt.slice(0, 10))}. Coupons are still paid to you until it is sold.`}
            </Notice>
            {open.status === "requested" ? (
              <Button
                label="Cancel the sale"
                variant="secondary"
                height={48}
                loading={cancelling}
                onPress={() => void cancel(open)}
                testID="bond-sale-cancel"
              />
            ) : null}
          </View>
        ) : null}

        {active ? (
          <View style={{ gap: 8 }} testID="coupon-destination">
            <Eyebrow>Coupons are paid to</Eyebrow>
            {accounts.length === 0 ? (
              <NoAccount />
            ) : (
              <AccountPicker
                accounts={accounts}
                chosen={holding.couponDestinationId}
                onChoose={(next) => void choose(next)}
                allowWaiting
                locked={saving}
              />
            )}
            <Txt size={fontSizes.xs} color={colors.muted}>
              {holding.couponDestinationId
                ? "Each coupon is sent here on its date. You can change it at any time."
                : "Choose an account, and each coupon is sent there on its date. Until then, coupons wait for you."}
            </Txt>
          </View>
        ) : null}

        {error ? <Notice tone="danger">{error}</Notice> : null}

        <View style={{ gap: 8 }} testID="bond-coupons">
          <Eyebrow>Coupons</Eyebrow>
          {coupons.length === 0 ? (
            <Txt size={fontSizes.sm} color={colors.muted}>
              {holding.nextCouponDate
                ? `No coupons yet. The first is paid on ${formatDay(holding.nextCouponDate)}.`
                : "No coupons were paid to this holding."}
            </Txt>
          ) : (
            coupons.map((coupon) => <CouponRow key={coupon.id} coupon={coupon} onSend={() => router.push(payOutHref("coupon", coupon.id))} />)
          )}
        </View>
      </View>
    </Screen>
  );
}

function CouponRow({ coupon, onSend }: { coupon: BondCoupon; onSend: () => void }) {
  const held = coupon.status === "held";
  return (
    <View style={styles.row} testID={`coupon-${coupon.id}`}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
        <View style={{ flex: 1, gap: 2 }}>
          <Txt weight="bold">{formatDay(coupon.couponDate)}</Txt>
          <Txt size={fontSizes.xs} color={held ? colors.warningText : colors.muted}>
            {held ? heldLabel(coupon.heldBecause) : "Paid to your account"}
          </Txt>
        </View>
        <Txt weight="extrabold">{formatAmount(coupon.amount)}</Txt>
      </View>
      {held ? <Button label="Send it" height={44} onPress={onSend} testID={`coupon-send-${coupon.id}`} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  black: { backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 },
  row: {
    gap: 12,
    padding: 16,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
});
