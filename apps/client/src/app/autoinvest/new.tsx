import { useEffect, useMemo, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164, nationalDigits } from "@/auth/phone";
import { useAutoinvest } from "@/autoinvest/AutoinvestProvider";
import {
  firstRun,
  formatLongDay,
  kampalaToday,
  MAX_RUN_MINOR,
  ordinal,
  parseShillings,
  scheduleLabel,
  WEEKDAYS,
} from "@/autoinvest/schedule";
import {
  AtMaturity,
  AutoinvestError,
  describeAutoinvestError,
  Frequency,
  PlanProduct,
} from "@/autoinvest/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Field, Input } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatMoney, networkName, networkProduct } from "@/payments/format";
import { newIdempotencyKey } from "@/payments/idempotency";
import { Loading } from "@/payments/Parts";
import { Network } from "@/payments/types";
import { colors, fontSizes, radii } from "@/theme";

const NETWORKS: Network[] = ["mtn", "airtel"];
const MONTH_DAYS = [1, 5, 10, 15, 20, 25, 28, 31];

/** A refusal means nothing was saved. Anything else may have reached us before the answer was lost. */
function outcomeUnknown(error: unknown): boolean {
  return !(error instanceof AutoinvestError) || error.code === "unavailable";
}

function likelyNetwork(national: string): Network {
  return /^7[045]/.test(national) ? "airtel" : "mtn";
}

/**
 * Start a plan. The client picks the product, the amount, how often and on
 * which day, and confirms the network. The paying number is always the one on
 * their own account. Nothing is collected today: the first prompt arrives on
 * the first due day after today.
 */
export default function NewPlan() {
  const router = useRouter();
  const client = useAutoinvest();
  const { session } = useAuth();
  const payer = session?.phone ?? "";

  const [products, setProducts] = useState<PlanProduct[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [productCode, setProductCode] = useState<string>("TREA-91");
  const [amountText, setAmountText] = useState("");
  const [frequency, setFrequency] = useState<Frequency>("monthly");
  const [monthDay, setMonthDay] = useState(28);
  const [weekday, setWeekday] = useState(5);
  const [atMaturity, setAtMaturity] = useState<AtMaturity>("roll_over");
  const [network, setNetwork] = useState<Network>(likelyNetwork(nationalDigits(payer)));
  const [amountError, setAmountError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Set once a try may have been saved before its answer was lost. From then on every try must
  // be the same plan under the same key, so nothing on the form can change.
  const [locked, setLocked] = useState(false);
  const [idempotencyKey] = useState(newIdempotencyKey);

  useEffect(() => {
    client
      .listProducts()
      .then(setProducts, (caught) => setLoadError(describeAutoinvestError(caught)));
  }, [client]);

  const product = products?.find((candidate) => candidate.code === productCode) ?? null;
  const day = frequency === "monthly" ? monthDay : weekday;
  const first = useMemo(() => firstRun(frequency, day, kampalaToday()), [frequency, day]);

  const check = (): number | null => {
    const amount = parseShillings(amountText);
    if (amount === null) {
      setAmountError("Enter an amount in whole shillings.");
      return null;
    }
    if (product && amount < product.minimum.minor) {
      setAmountError(`The ${product.name} takes at least ${formatMoney(product.minimum)}.`);
      return null;
    }
    if (amount > MAX_RUN_MINOR) {
      setAmountError("A plan can collect up to UGX 5,000,000 at a time.");
      return null;
    }
    setAmountError(null);
    return amount;
  };

  const start = async () => {
    setError(null);
    const amount = check();
    if (amount === null || !product) {
      return;
    }
    setBusy(true);
    try {
      const plan = await client.createPlan({
        productCode: product.code,
        amount: { currency: "UGX", minor: amount },
        frequency,
        day,
        network,
        payer,
        atMaturity,
        idempotencyKey,
      });
      router.replace(`/autoinvest/${plan.id}`);
    } catch (caught) {
      if (
        caught instanceof AutoinvestError &&
        (caught.code === "below_minimum" || caught.code === "amount_too_large")
      ) {
        setAmountError(describeAutoinvestError(caught));
      } else {
        setError(describeAutoinvestError(caught));
      }
      if (outcomeUnknown(caught)) {
        setLocked(true);
      }
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        products ? (
          <Button
            label="Start plan"
            variant="lime"
            onPress={start}
            loading={busy}
            testID="plan-start"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "New plan" }} />
      <BackButton fallback="/autoinvest" />
      <View style={{ marginTop: 20 }}>
        <Title>New plan</Title>
      </View>
      {loadError ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">{loadError}</Notice>
        </View>
      ) : !products ? (
        <Loading label="Loading products" />
      ) : (
        <View style={{ marginTop: 20, gap: 18 }}>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          {locked ? (
            <Notice tone="warning">
              Your plan may already have reached us, so its details are fixed. Pressing Start plan
              again sends the same plan. It never makes two.
            </Notice>
          ) : null}

          <View style={{ gap: 8 }} accessibilityRole="radiogroup">
            <Eyebrow>Invest in</Eyebrow>
            {products.map((option) => (
              <Choice
                disabled={locked}
                key={option.code}
                selected={option.code === productCode}
                onPress={() => setProductCode(option.code)}
                title={option.name}
                note={`${option.ratePercent}% a year after tax. From ${formatMoney(option.minimum)}.`}
                testID={`product-${option.code}`}
              />
            ))}
          </View>

          <Field
            label="Amount each time"
            hint={
              product ? `At least ${formatMoney(product.minimum)}, up to UGX 5,000,000.` : undefined
            }
            error={amountError}
          >
            <Input
              label="Amount each time, in shillings"
              value={amountText}
              onChangeText={(text) => {
                setAmountText(text);
                setAmountError(null);
              }}
              keyboardType="number-pad"
              placeholder="250,000"
              invalid={Boolean(amountError)}
              editable={!locked}
              testID="plan-amount"
            />
          </Field>

          <View style={{ gap: 8 }}>
            <Eyebrow>How often</Eyebrow>
            <View style={styles.segments} accessibilityRole="radiogroup">
              {(["monthly", "weekly"] as Frequency[]).map((option) => (
                <Segment
                  disabled={locked}
                  key={option}
                  label={option === "monthly" ? "Every month" : "Every week"}
                  selected={frequency === option}
                  onPress={() => setFrequency(option)}
                  testID={`frequency-${option}`}
                />
              ))}
            </View>
            <View
              style={styles.chips}
              accessibilityRole="radiogroup"
              accessibilityLabel="Which day"
            >
              {frequency === "monthly"
                ? MONTH_DAYS.map((option) => (
                    <Chip
                      disabled={locked}
                      key={option}
                      label={ordinal(option)}
                      selected={monthDay === option}
                      onPress={() => setMonthDay(option)}
                      testID={`day-${option}`}
                    />
                  ))
                : WEEKDAYS.map((name, index) => (
                    <Chip
                      disabled={locked}
                      key={name}
                      label={name.slice(0, 3)}
                      accessibilityLabel={name}
                      selected={weekday === index + 1}
                      onPress={() => setWeekday(index + 1)}
                      testID={`weekday-${index + 1}`}
                    />
                  ))}
            </View>
          </View>

          <View style={{ gap: 8 }} accessibilityRole="radiogroup">
            <Eyebrow>When each one matures</Eyebrow>
            <Choice
              disabled={locked}
              selected={atMaturity === "roll_over"}
              onPress={() => setAtMaturity("roll_over")}
              title="Roll it over"
              note="It keeps earning in a new bill"
              testID="maturity-roll_over"
            />
            <Choice
              disabled={locked}
              selected={atMaturity === "pay_out"}
              onPress={() => setAtMaturity("pay_out")}
              title="Pay it out"
              note="To your mobile money or bank account"
              testID="maturity-pay_out"
            />
          </View>

          <View style={{ gap: 8 }} accessibilityRole="radiogroup">
            <Eyebrow>Pay from</Eyebrow>
            <View style={styles.segments}>
              {NETWORKS.map((option) => (
                <Segment
                  disabled={locked}
                  key={option}
                  label={networkProduct[option]}
                  selected={network === option}
                  onPress={() => setNetwork(option)}
                  testID={`network-${option}`}
                />
              ))}
            </View>
            <Txt size={fontSizes.xs} color={colors.muted}>
              {`${networkName[network]} sends each prompt to ${formatE164(payer)}, the number on your Finvest account.`}
            </Txt>
          </View>

          <View style={styles.summary} testID="plan-summary">
            <Txt size={15} weight="extrabold" color={colors.limeText}>
              {`${scheduleLabel(frequency, day)}, from ${formatLongDay(first)}`}
            </Txt>
            <Txt size={13} color={colors.limeText}>
              Nothing is collected today. On each day we send a prompt between 8 am and 8 pm, and
              nothing is taken until you approve it. If a payment fails we try once more the next
              day, and never take a day twice.
            </Txt>
          </View>
        </View>
      )}
    </Screen>
  );
}

type ChoiceProps = {
  selected: boolean;
  disabled: boolean;
  onPress: () => void;
  title: string;
  note: string;
  testID: string;
};

function Choice({ selected, disabled, onPress, title, note, testID }: ChoiceProps) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected, disabled }}
      accessibilityLabel={title}
      onPress={onPress}
      disabled={disabled}
      testID={testID}
      style={({ pressed }) => [
        styles.choice,
        selected && styles.choiceSelected,
        pressed && { opacity: 0.8 },
        disabled && !selected && styles.faded,
      ]}
    >
      <View style={{ flex: 1 }}>
        <Txt weight="bold">{title}</Txt>
        <Txt size={13} color={colors.muted}>
          {note}
        </Txt>
      </View>
      <View style={[styles.radio, selected && styles.radioSelected]} />
    </Pressable>
  );
}

type SegmentProps = {
  label: string;
  selected: boolean;
  disabled: boolean;
  onPress: () => void;
  testID: string;
};

function Segment({ label, selected, disabled, onPress, testID }: SegmentProps) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected, disabled }}
      onPress={onPress}
      disabled={disabled}
      testID={testID}
      style={[
        styles.segment,
        selected && styles.segmentSelected,
        disabled && !selected && styles.faded,
      ]}
    >
      <Txt size={14} weight="bold" color={selected ? colors.white : colors.ink}>
        {label}
      </Txt>
    </Pressable>
  );
}

function Chip({
  label,
  selected,
  disabled,
  onPress,
  testID,
  accessibilityLabel,
}: SegmentProps & { accessibilityLabel?: string }) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ checked: selected, disabled }}
      onPress={onPress}
      disabled={disabled}
      testID={testID}
      style={[
        styles.chip,
        selected && styles.segmentSelected,
        disabled && !selected && styles.faded,
      ]}
    >
      <Txt size={14} weight="bold" color={selected ? colors.white : colors.ink}>
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  choice: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: 60,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  choiceSelected: { borderWidth: 2, borderColor: colors.black },
  radio: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: colors.borderStrong,
  },
  radioSelected: { borderWidth: 7, borderColor: colors.black },
  segments: { flexDirection: "row", gap: 8 },
  faded: { opacity: 0.4 },
  segment: {
    flex: 1,
    minHeight: 48,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 8,
  },
  segmentSelected: { backgroundColor: colors.black, borderColor: colors.black },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minWidth: 56,
    minHeight: 44,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12,
  },
  summary: {
    backgroundColor: colors.limeSurface,
    borderRadius: radii.lg,
    padding: 14,
    gap: 6,
  },
});
