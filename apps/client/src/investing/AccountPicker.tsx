import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Txt } from "@/components/Txt";
import { formatDateTime } from "@/payments/format";
import { colors, fontSizes, radii } from "@/theme";

import { PayoutAccount } from "./types";

type Props = {
  accounts: PayoutAccount[];
  chosen: string | null;
  onChoose: (accountId: string) => void;
  /** Accounts still in their first day can be chosen for later, such as for maturity, but not paid now. */
  allowWaiting?: boolean;
  /** After a try whose outcome is unknown, so the request cannot change. */
  locked?: boolean;
};

/** The client's approved accounts as radio buttons. */
export function AccountPicker({ accounts, chosen, onChoose, allowWaiting = false, locked = false }: Props) {
  return (
    <View style={{ gap: 8 }} accessibilityRole="radiogroup">
      {accounts.map((account) => {
        const choosable = allowWaiting || account.ready;
        const selected = choosable && account.id === chosen;
        const disabled = locked || !choosable;
        return (
          <Pressable
            key={account.id}
            accessibilityRole="radio"
            accessibilityState={{ checked: selected, disabled }}
            accessibilityLabel={account.label}
            disabled={disabled}
            onPress={() => onChoose(account.id)}
            testID={`account-${account.id}`}
            style={({ pressed }) => [
              styles.option,
              selected && styles.selected,
              !choosable && styles.waiting,
              pressed && { opacity: 0.85 },
            ]}
          >
            <View style={[styles.radio, selected && styles.radioOn]} />
            <View style={{ flex: 1, gap: 2 }}>
              <Txt size={fontSizes.md} weight="bold">
                {account.label}
              </Txt>
              <Txt size={fontSizes.sm} color={colors.muted}>
                {account.ready
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
      })}
    </View>
  );
}

/** The first account that can be chosen, for a default. */
export function firstChoosable(accounts: PayoutAccount[], allowWaiting = false): string | null {
  return accounts.find((account) => allowWaiting || account.ready)?.id ?? null;
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

/** No approved account yet: where to add one. */
export function NoAccount() {
  const router = useRouter();
  return (
    <View style={{ gap: 12 }}>
      <Notice tone="warning">
        You need an approved account to be paid to. Add your mobile money number or bank account. We check it is in
        your name, and it can be paid one day after it is approved.
      </Notice>
      <Button
        label="Add a payout account"
        variant="secondary"
        onPress={() => router.push("/payouts/add")}
        testID="add-payout-account"
      />
    </View>
  );
}
