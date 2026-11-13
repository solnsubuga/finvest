import { StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Logo } from "@/components/Logo";
import { Screen } from "@/components/Screen";
import { Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

export default function Welcome() {
  const router = useRouter();
  return (
    <Screen
      dark
      footer={
        <View style={{ gap: 12 }}>
          <Button label="Create an account" variant="lime" onPress={() => router.push("/signup")} />
          <Button label="Log in" variant="outlineOnDark" onPress={() => router.push("/login")} />
        </View>
      }
    >
      <Stack.Screen options={{ title: "Finvest", contentStyle: { backgroundColor: colors.black } }} />
      <View style={{ paddingTop: 16 }}>
        <Logo onDark />
      </View>

      {/* An illustration of the product, not the client's own figures. */}
      <View style={styles.cards} accessible accessibilityLabel="Example: a 91 day Treasury bill growing every day, paid for from mobile money">
        <View style={styles.example}>
          <Txt size={11} weight="bold" color={colors.white} style={{ letterSpacing: 0.6 }}>
            EXAMPLE
          </Txt>
        </View>
        <View style={styles.billCard}>
          <Txt size={fontSizes.xs} weight="semibold" color={colors.muted}>
            91 Day Treasury Bill
          </Txt>
          <Txt size={26} weight="extrabold" lineHeight={32} style={{ letterSpacing: -0.5 }}>
            UGX 5,049,486
          </Txt>
          <Txt size={fontSizes.xs} weight="bold" color={colors.black}>
            +1,455 today, after tax
          </Txt>
        </View>
        <View style={styles.momoCard}>
          <View style={styles.momoTick}>
            <Icon name="check" size={18} color={colors.lime} strokeWidth={2.5} />
          </View>
          <View>
            <Txt size={14} weight="bold">
              Paid from MoMo
            </Txt>
            <Txt size={fontSizes.xs}>UGX 200,000, in seconds</Txt>
          </View>
        </View>
      </View>

      <Txt
        accessibilityRole="header"
        size={40}
        weight="extrabold"
        lineHeight={42}
        color={colors.white}
        style={{ marginTop: 44, letterSpacing: -1.2 }}
      >
        Your money, working every day.
      </Txt>
      <Txt size={fontSizes.lg} lineHeight={24} color={colors.onBlackMuted} style={{ marginTop: 14 }}>
        Pay with mobile money, invest in treasury bills and funds, and watch your interest grow
        every day.
      </Txt>
    </Screen>
  );
}

const styles = StyleSheet.create({
  cards: { marginTop: 32, gap: 12 },
  example: {
    alignSelf: "flex-start",
    borderWidth: 1,
    borderColor: colors.onBlackBorder,
    borderRadius: 999,
    paddingVertical: 3,
    paddingHorizontal: 10,
  },
  billCard: {
    width: 260,
    backgroundColor: colors.white,
    borderRadius: 22,
    padding: 18,
    gap: 4,
  },
  momoCard: {
    width: 240,
    alignSelf: "flex-end",
    backgroundColor: colors.lime,
    borderRadius: 22,
    paddingVertical: 16,
    paddingHorizontal: 18,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  momoTick: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.ink,
    alignItems: "center",
    justifyContent: "center",
  },
});
