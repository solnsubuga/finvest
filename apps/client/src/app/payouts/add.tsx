import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatNational, nationalDigits } from "@/auth/phone";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { usePinConfirmation } from "@/components/pin/ConfirmWithPin";
import { Button } from "@/components/Button";
import { Field, PhoneField, TextField } from "@/components/Field";
import { LoadFailed } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { Loading } from "@/payments/Parts";
import { useFocusLoaded } from "@/payouts/Parts";
import { usePayouts } from "@/payouts/PayoutsProvider";
import { AccountKind, describePayoutsError, PayoutsError, PayoutsErrorCode } from "@/payouts/types";
import { colors, fontSizes, radii } from "@/theme";

type Problems = Partial<Record<"phone" | "bank" | "account" | "name", string>>;

const FIELD_OF: Partial<Record<PayoutsErrorCode, keyof Problems>> = {
  phone_invalid: "phone",
  phone_network_unsupported: "phone",
  bank_invalid: "bank",
  account_number_invalid: "account",
  holder_name_invalid: "name",
};

/**
 * Add a mobile money number or a bank account. Saving it texts a code at once; the account
 * screen takes the code, then the proof. Nothing here can be paid until staff approve it.
 */
export default function AddPayoutAccount() {
  const router = useRouter();
  const client = usePayouts();
  const { session } = useAuth();
  const [kind, setKind] = useState<AccountKind>("mobile_money");
  // Most clients are paid to the number they signed up with.
  const [phone, setPhone] = useState(() => (session ? formatNational(session.phone) : ""));
  const [bankCode, setBankCode] = useState<string | null>(null);
  const [accountNumber, setAccountNumber] = useState("");
  const [holderName, setHolderName] = useState("");
  const [problems, setProblems] = useState<Problems>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { ask: askPin, screen: pinScreen } = usePinConfirmation();
  // The banks come from the backend, which refuses a code it does not list.
  const [banks, retryBanks] = useFocusLoaded(useCallback(() => client.listBanks(), [client]));

  const check = (): Problems => {
    const found: Problems = {};
    if (kind === "mobile_money" && nationalDigits(phone).length !== 9) found.phone = describePayoutsError(new PayoutsError("phone_invalid"));
    if (kind === "bank" && !bankCode) found.bank = describePayoutsError(new PayoutsError("bank_invalid"));
    if (kind === "bank" && !/^[0-9]{6,20}$/.test(accountNumber.replace(/[ -]/g, ""))) {
      found.account = describePayoutsError(new PayoutsError("account_number_invalid"));
    }
    if (!holderName.trim()) found.name = describePayoutsError(new PayoutsError("holder_name_invalid"));
    return found;
  };

  const save = async () => {
    const found = check();
    setProblems(found);
    setError(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    // On a phone with a PIN, adding a payout account is confirmed with it too.
    const answer = await askPin("payout_account");
    if (answer === "cancelled") {
      setBusy(false);
      return;
    }
    try {
      const account = await client.addAccount(
        kind === "mobile_money"
          ? { kind, phone: `+256${nationalDigits(phone)}`, holderName, pinConfirmation: answer.confirmation }
          : {
              kind,
              bankCode: bankCode ?? "",
              accountNumber: accountNumber.replace(/[ -]/g, ""),
              holderName,
              pinConfirmation: answer.confirmation,
            },
      );
      // Text the code straight away. If that fails the account screen offers to send it.
      let sent = true;
      try {
        await client.sendCode(account.id);
      } catch {
        sent = false;
      }
      router.replace({ pathname: "/payouts/[id]", params: { id: account.id, sent: sent ? "1" : "0" } });
    } catch (caught) {
      const code = caught instanceof PayoutsError ? caught.code : "unavailable";
      const field = FIELD_OF[code];
      if (field) {
        setProblems({ [field]: describePayoutsError(caught) });
      } else {
        // Saving has no idempotency key: if the answer was lost the account may be saved, and
        // trying again says it already is, which sends the client to the list to finish it.
        setError(describePayoutsError(caught));
      }
      setBusy(false);
    }
  };

  if (pinScreen) return pinScreen;

  return (
    <Screen
      signedIn
      footer={<Button label="Save and text me a code" onPress={() => void save()} loading={busy} testID="payout-account-save" />}
    >
      <Stack.Screen options={{ title: "Add a payout account" }} />
      <BackButton fallback="/payouts" />
      <View style={{ marginTop: 20, gap: 8 }}>
        <Title>Add a payout account</Title>
        <Txt size={fontSizes.md} color={colors.muted}>
          It must be in your own name, exactly as on your ID. We never pay an account in another name.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 18 }}>
        <View style={styles.segment} accessibilityRole="radiogroup">
          {(
            [
              ["mobile_money", "Mobile money"],
              ["bank", "Bank account"],
            ] as const
          ).map(([value, label]) => (
            <Pressable
              key={value}
              accessibilityRole="radio"
              aria-checked={kind === value}
              onPress={() => {
                setKind(value);
                setProblems({});
                setError(null);
              }}
              style={[styles.segmentOption, kind === value && styles.segmentOn]}
              testID={`payout-kind-${value}`}
            >
              <Txt size={fontSizes.sm} weight="bold" color={kind === value ? colors.white : colors.ink}>
                {label}
              </Txt>
            </Pressable>
          ))}
        </View>

        {kind === "mobile_money" ? (
          <PhoneField
            value={phone}
            onChangeText={setPhone}
            hint="MTN or Airtel. We text the code to this number."
            error={problems.phone}
          />
        ) : (
          <>
            <Field label="Bank" error={problems.bank}>
              {banks.state === "loading" ? (
                <Loading label="Loading the banks" />
              ) : banks.state === "failed" ? (
                <LoadFailed message={banks.message} onRetry={retryBanks} />
              ) : (
                <View style={styles.banks} accessibilityRole="radiogroup">
                  {banks.value.map((bank) => {
                    const on = bank.code === bankCode;
                    return (
                      <Pressable
                        key={bank.code}
                        accessibilityRole="radio"
                        aria-checked={on}
                        onPress={() => setBankCode(bank.code)}
                        style={[styles.bank, on && styles.bankOn]}
                        testID={`bank-${bank.code}`}
                      >
                        <Txt size={fontSizes.sm} weight="semibold" color={on ? colors.white : colors.ink}>
                          {bank.name}
                        </Txt>
                      </Pressable>
                    );
                  })}
                </View>
              )}
            </Field>
            <TextField
              label="Account number"
              value={accountNumber}
              onChangeText={(text) => setAccountNumber(text.replace(/[^0-9 -]/g, "").slice(0, 26))}
              keyboardType="number-pad"
              inputMode="numeric"
              placeholder="9030012345678"
              hint="We text the code to your own mobile number."
              error={problems.account}
              testID="account-number-input"
            />
          </>
        )}

        <TextField
          label="Name on the account"
          value={holderName}
          onChangeText={setHolderName}
          autoComplete="name"
          placeholder="As on your ID"
          hint="We compare the name on your proof with your ID. A different name is always refused."
          error={problems.name}
          maxLength={120}
          testID="holder-name-input"
        />
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  segment: {
    flexDirection: "row",
    backgroundColor: colors.surfaceSunken,
    borderRadius: radii.lg,
    padding: 4,
    gap: 4,
  },
  segmentOption: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 44,
    paddingVertical: 12,
    borderRadius: radii.lg - 4,
  },
  segmentOn: { backgroundColor: colors.black },
  banks: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  bank: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    minHeight: 44,
    justifyContent: "center",
    borderRadius: 20,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  bankOn: { backgroundColor: colors.black, borderColor: colors.black },
});
