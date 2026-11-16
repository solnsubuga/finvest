import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useBonds } from "@/bonds/BondsProvider";
import { couponsLabel, maturesLabel, notBuyableLabel, priceLabel } from "@/bonds/format";
import { QuoteFigures } from "@/bonds/Parts";
import { Bond, describeBondsError } from "@/bonds/types";
import { BondCalc, useBondQuote } from "@/bonds/useBondQuote";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Field, Input } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatAmount, formatDay, groupDigits, parseWholeAmount, wholeUnits } from "@/investing/format";
import { Failed } from "@/investing/Parts";
import { useLoaded } from "@/investing/useLoaded";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/**
 * A bond issue, with a calculator: type a face value and see what it costs today and pays to
 * maturity, after tax. The figures are the backend's quote. The app works out none of them.
 */
export default function BondDetail() {
  const router = useRouter();
  const { code } = useLocalSearchParams<{ code: string }>();
  const client = useBonds();
  const [loaded, retry] = useLoaded(
    useCallback(() => client.getBond(code), [client, code]),
    describeBondsError,
  );
  const bond = loaded.state === "ready" ? loaded.value : undefined;

  const [text, setText] = useState("");
  const face = bond ? parseWholeAmount(text, bond.currency) : null;
  const calc = useBondQuote(client, bond, face);
  const ready = calc.state === "ready" && bond?.canBuy === true;

  return (
    <Screen
      signedIn
      footer={
        bond?.canBuy ? (
          <Button
            label="Review"
            variant="lime"
            disabled={!ready}
            onPress={() =>
              router.push({ pathname: "/invest/bond/review", params: { code: bond.code, face: String(face?.minor ?? "") } })
            }
            testID="bond-review"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: bond?.name ?? "Treasury bond" }} />
      <BackButton fallback="/invest" />
      {loaded.state === "loading" ? (
        <Loading label="Loading the bond" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : bond ? (
        <View style={{ marginTop: 20, gap: 18 }}>
          <View style={{ gap: 6 }}>
            <Eyebrow>{`Treasury bond · ${maturesLabel(bond)}`}</Eyebrow>
            <Title>{bond.name}</Title>
          </View>
          <Headline bond={bond} />
          <Facts bond={bond} />
          {notBuyableLabel(bond) ? (
            <Notice tone="warning">{closedSentence(bond)}</Notice>
          ) : (
            <>
              <Field
                label="How much face value do you want?"
                hint={`From ${formatAmount(bond.minimumFace)}, in steps of ${formatAmount(bond.faceStep)}. You pay today's price for it.`}
                error={calc.state === "failed" ? calc.message : text && !face ? "Type a whole amount, such as 1,000,000." : null}
              >
                <Input
                  label="Face value"
                  value={text}
                  onChangeText={(typed) => setText(groupDigits(typed))}
                  keyboardType="number-pad"
                  placeholder={wholeUnits(bond.minimumFace)}
                  invalid={calc.state === "failed"}
                  testID="face"
                />
              </Field>
              <Calculation calc={calc} />
            </>
          )}
        </View>
      ) : null}
    </Screen>
  );
}

function closedSentence(bond: Bond): string {
  if (bond.status === "paused") return "This bond is paused for new buyers. You can still see its terms.";
  if (bond.price === null) return "This bond has no price today, so it cannot be bought yet. Look again later.";
  return "This bond is too close to maturity to buy: its last coupon period has begun.";
}

function Headline({ bond }: { bond: Bond }) {
  return (
    <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
      <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
        Yearly coupon after tax
      </Txt>
      <Txt size={40} weight="extrabold" lineHeight={46} color={colors.lime} style={{ letterSpacing: -1 }}>
        {`${bond.couponPercent}%`}
      </Txt>
      <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
        {`Of the face value, paid in two coupons a year until ${formatDay(bond.maturityDate)}, when the face value is paid back.`}
      </Txt>
    </View>
  );
}

/** The issue's terms at a glance. */
function Facts({ bond }: { bond: Bond }) {
  return (
    <View style={{ gap: 8 }} testID="bond-facts">
      <Txt size={fontSizes.sm} weight="bold" color={colors.muted} accessibilityRole="header">
        About this bond
      </Txt>
      <DetailList
        rows={[
          { label: "Issued by", value: "Bank of Uganda, for the Government" },
          { label: "ISIN", value: bond.isin },
          { label: "Matures", value: formatDay(bond.maturityDate) },
          { label: "Coupons", value: couponsLabel(bond) },
          { label: "Coupons left", value: String(bond.couponsLeft) },
          { label: "Price today", value: priceLabel(bond.price) },
          { label: "Smallest face value", value: formatAmount(bond.minimumFace) },
          { label: "Need it early?", value: "Finvest sells it for you at the market price" },
        ]}
      />
    </View>
  );
}

function Calculation({ calc }: { calc: BondCalc }) {
  if (calc.state === "idle") {
    return (
      <Txt size={fontSizes.sm} color={colors.muted}>
        Type a face value to see what it costs and pays.
      </Txt>
    );
  }
  if (calc.state === "working") {
    return <Loading label="Working out what it costs" />;
  }
  if (calc.state === "failed") {
    return null;
  }
  return <QuoteFigures calc={calc} />;
}
