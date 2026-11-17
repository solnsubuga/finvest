import { useCallback } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { formatAmount, formatDay } from "@/investing/format";
import { useFocusLoaded, useLoaded } from "@/investing/useLoaded";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";

import { useBonds } from "./BondsProvider";
import { formatPrice, holdingLine, holdingStatusLabel, maturesLabel, notBuyableLabel, priceLabel } from "./format";
import {
  Bond,
  BondCoupon,
  BondHolding,
  BondRepayment,
  BondSale,
  bondPayableId,
  describeBondsError,
  HeldKind,
} from "./types";
import { BondCalc } from "./useBondQuote";

/** One issue in the list: its name, the coupon after tax, when it matures and today's price. */
export function BondCard({ bond, onPress }: { bond: Bond; onPress: () => void }) {
  const closed = notBuyableLabel(bond);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${bond.name}, ${bond.couponPercent}% a year after tax, ${maturesLabel(bond).toLowerCase()}${closed ? `, ${closed.toLowerCase()}` : ""}`}
      onPress={onPress}
      testID={`bond-${bond.code}`}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}
    >
      <View style={{ flex: 1, gap: 4 }}>
        <Txt size={fontSizes.xs} weight="bold" color={colors.muted}>
          {`Treasury bond · ${maturesLabel(bond)}`}
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {bond.name}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {closed ?? `From ${formatAmount(bond.minimumFace)} · price ${priceLabel(bond.price)}`}
        </Txt>
        {closed ? null : (
          <Txt size={fontSizes.xs} color={colors.muted}>
            Coupon paid every six months
          </Txt>
        )}
      </View>
      <View style={{ alignItems: "flex-end", gap: 2 }}>
        <Txt size={24} weight="extrabold" lineHeight={30} style={{ letterSpacing: -0.5 }}>
          {`${bond.couponPercent}%`}
        </Txt>
        <Txt size={fontSizes.xs} weight="semibold" color={colors.muted}>
          coupon after tax
        </Txt>
      </View>
      <Icon name="chevron" size={18} color={colors.muted} />
    </Pressable>
  );
}

/**
 * The bonds on Invest, under the bills. They load on their own, so a bond list that fails
 * leaves the bills usable, and an empty one shows nothing at all.
 */
export function BondsSection({ columns = 1 }: { columns?: number }) {
  const router = useRouter();
  const client = useBonds();
  const [bonds, retry] = useLoaded(
    useCallback(() => client.listBonds(), [client]),
    describeBondsError,
  );
  if (bonds.state === "ready" && bonds.value.length === 0) return null;
  return (
    <View style={{ gap: 12 }} testID="bonds">
      <View style={{ gap: 2 }}>
        <Txt size={fontSizes.lg} weight="extrabold" accessibilityRole="header">
          Treasury bonds
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          Buy part of a Bank of Uganda bond. It pays a coupon every six months, and the face value when it matures.
        </Txt>
      </View>
      {bonds.state === "loading" ? (
        <Loading label="Loading bonds" />
      ) : bonds.state === "failed" ? (
        <View style={{ gap: 12 }}>
          <Notice tone="danger">{bonds.message}</Notice>
          <Button label="Try again" onPress={retry} height={48} testID="bonds-retry" />
        </View>
      ) : (
        <View style={columns > 1 ? styles.grid : { gap: 12 }}>
          {bonds.value.map((bond) => (
            <View key={bond.code} style={columns > 1 ? { flexBasis: `${100 / columns - 2}%`, flexGrow: 1 } : undefined}>
              <BondCard bond={bond} onPress={() => router.push(`/invest/bond/${bond.code}`)} />
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

/** One of the client's bond holdings in Portfolio. `note` says what waits on it, if anything. */
export function HoldingCard({ holding, note, onPress }: { holding: BondHolding; note?: string | null; onPress: () => void }) {
  const waiting = holding.status === "unpaid";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${holding.name}, ${holdingStatusLabel[holding.status]}, face value ${formatAmount(holding.face)}, ${holdingLine(holding)}${note ? `, ${note}` : ""}`}
      onPress={onPress}
      testID={`holding-${holding.id}`}
      style={({ pressed }) => [styles.card, { alignItems: "flex-start" }, pressed && { opacity: 0.85 }]}
    >
      <View style={{ flex: 1, gap: 4 }}>
        <Txt size={fontSizes.xs} weight="bold" color={waiting ? colors.warningText : colors.muted}>
          {holdingStatusLabel[holding.status]}
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {holding.name}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {waiting ? `Pay ${formatAmount(holding.cost)} to buy it` : holdingLine(holding)}
        </Txt>
        {note ? (
          <Txt size={fontSizes.xs} weight="bold" color={colors.warningText}>
            {note}
          </Txt>
        ) : null}
      </View>
      <View style={{ alignItems: "flex-end", gap: 2 }}>
        <Txt size={fontSizes.lg} weight="extrabold">
          {formatAmount(holding.face)}
        </Txt>
        <Txt size={fontSizes.xs} weight="semibold" color={colors.muted}>
          face value
        </Txt>
      </View>
    </Pressable>
  );
}

type Holdings = { holdings: BondHolding[]; coupons: BondCoupon[]; repayments: BondRepayment[]; sales: BondSale[] };

/** The sale of a holding that is still going on, if any. */
export function openSaleOf(sales: BondSale[], holdingId: string): BondSale | undefined {
  return sales.find((sale) => sale.holdingId === holdingId && ["requested", "priced", "approved"].includes(sale.status));
}

/** The pay-out screen for money that waits for an account. */
export function payOutHref(kind: HeldKind, id: string): string {
  return `/investment/bond/pay-out?kind=${kind}&id=${encodeURIComponent(id)}`;
}

/**
 * Whether the client holds a bond, waiting for payment or running. The investment totals leave
 * bonds out, so a screen that shows them says so. Null until known, or when bonds cannot be read.
 */
export function useHoldsBonds(): boolean | null {
  const client = useBonds();
  const [loaded] = useFocusLoaded(useCallback(() => client.listHoldings(), [client]), describeBondsError);
  return loaded.state === "ready" ? loaded.value.some((holding) => holding.status === "unpaid" || holding.status === "active") : null;
}

/** Said beside totals that leave the client's bonds out. */
export function BondsLeftOut({ below = false }: { below?: boolean }) {
  return (
    <Txt size={fontSizes.xs} color={colors.muted} testID="bonds-left-out">
      {below
        ? "These totals leave out your bonds. Each bond has its own figures under Bonds below."
        : "These totals leave out your bonds. Each bond has its own figures in Portfolio."}
    </Txt>
  );
}

/**
 * The client's bonds in Portfolio: each holding, and money from a bond that waits for an
 * account. It loads on its own, so the investments above stay usable if it fails, and shows
 * nothing at all for a client with no bonds.
 */
export function BondHoldingsSection({ columns = 1 }: { columns?: number }) {
  const router = useRouter();
  const client = useBonds();
  const [loaded, retry] = useFocusLoaded(
    useCallback(async (): Promise<Holdings> => {
      const [holdings, coupons, repayments, sales] = await Promise.all([
        client.listHoldings(),
        client.listCoupons(),
        client.listRepayments(),
        client.listSales(),
      ]);
      return { holdings, coupons, repayments, sales };
    }, [client]),
    describeBondsError,
  );
  if (loaded.state === "loading") return null;
  if (loaded.state === "failed") {
    return (
      <View style={{ gap: 12 }} testID="bond-holdings">
        <Txt size={fontSizes.lg} weight="extrabold" accessibilityRole="header">
          Bonds
        </Txt>
        <Notice tone="danger">{loaded.message}</Notice>
        <Button label="Try again" onPress={retry} height={48} testID="bond-holdings-retry" />
      </View>
    );
  }
  const { holdings, coupons, repayments, sales } = loaded.value;
  const heldRepayments = repayments.filter((item) => item.status === "held");
  const heldSales = sales.filter((sale) => sale.status === "sold" && sale.payoutStatus === "held");
  if (holdings.length === 0 && heldRepayments.length === 0 && heldSales.length === 0) return null;
  const noteFor = (holding: BondHolding): string | null => {
    const held = coupons.filter((coupon) => coupon.holdingId === holding.id && coupon.status === "held");
    if (held.length) {
      return held.length === 1
        ? `A coupon of ${formatAmount(held[0]!.amount)} is waiting for an account`
        : `${held.length} coupons are waiting for an account`;
    }
    const sale = openSaleOf(sales, holding.id);
    if (sale) return sale.status === "approved" ? "Sold, being paid to you" : "You asked to sell it";
    if (holding.status === "active" && !holding.couponDestinationId) return "Choose where its coupons are paid";
    return null;
  };
  const cell = columns > 1 ? { flexBasis: `${100 / columns - 2}%` as const, flexGrow: 1 } : undefined;
  return (
    <View style={{ gap: 12 }} testID="bond-holdings">
      <Txt size={fontSizes.lg} weight="extrabold" accessibilityRole="header">
        Bonds
      </Txt>
      <View style={columns > 1 ? styles.grid : { gap: 12 }}>
        {holdings.map((holding) => (
          <View key={holding.id} style={cell}>
            <HoldingCard
              holding={holding}
              note={noteFor(holding)}
              onPress={() =>
                router.push(holding.status === "unpaid" ? `/pay/${bondPayableId(holding.id)}` : `/investment/bond/${holding.id}`)
              }
            />
          </View>
        ))}
        {heldRepayments.map((item) => (
          <View key={item.id} style={cell}>
            <WaitingCard
              title={item.name}
              line={`Repaid ${formatDay(item.repaidOn)}`}
              amount={item.amount}
              onPress={() => router.push(payOutHref("repayment", item.id))}
              testID={`repayment-${item.id}`}
            />
          </View>
        ))}
        {heldSales.map((sale) => (
          <View key={sale.id} style={cell}>
            <WaitingCard
              title={sale.name}
              line={sale.soldOn ? `Sold ${formatDay(sale.soldOn)}` : "Sold"}
              amount={sale.amount!}
              onPress={() => router.push(payOutHref("sale", sale.id))}
              testID={`sale-${sale.id}`}
            />
          </View>
        ))}
      </View>
    </View>
  );
}

/** Money from a bond that is no longer held, waiting for the client to name an account. */
function WaitingCard({
  title,
  line,
  amount,
  onPress,
  testID,
}: {
  title: string;
  line: string;
  amount: BondCoupon["amount"];
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${title}, ${line}, ${formatAmount(amount)} waiting for an account`}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [styles.card, { alignItems: "flex-start" }, pressed && { opacity: 0.85 }]}
    >
      <View style={{ flex: 1, gap: 4 }}>
        <Txt size={fontSizes.xs} weight="bold" color={colors.warningText}>
          Waiting for an account
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {title}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {line}
        </Txt>
      </View>
      <Txt size={fontSizes.lg} weight="extrabold">
        {formatAmount(amount)}
      </Txt>
    </Pressable>
  );
}

/** A quote's figures, as the detail and the review show them. */
export function QuoteFigures({ calc }: { calc: Extract<BondCalc, { state: "ready" }> }) {
  const { quote } = calc;
  return (
    <View style={{ gap: 8 }} testID="bond-quote">
      <DetailList
        rows={[
          { label: "Face value", value: formatAmount(quote.face) },
          { label: "Price", value: `${formatPrice(quote.price)} per 100` },
          { label: "Interest since the last coupon", value: formatAmount(quote.accrued) },
          { label: "You pay", value: formatAmount(quote.cost) },
          { label: "Each coupon after tax", value: formatAmount(quote.coupon) },
          { label: "Coupons left", value: `${quote.couponsLeft}, the next on ${formatDay(quote.nextCouponDate)}` },
          { label: "Back by maturity", value: formatAmount(quote.totalBack) },
          { label: "Gain by maturity", value: formatAmount(quote.gain) },
        ]}
      />
      <Txt size={fontSizes.xs} color={colors.muted}>
        {"If you paid today. You pay the interest earned since the last coupon on top of the price. The next coupon pays it back, less tax, with the rest of the six months' interest."}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.xl,
    padding: 18,
  },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
});
