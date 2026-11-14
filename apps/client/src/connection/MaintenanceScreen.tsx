import { StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Button } from "@/components/Button";
import { LogoMark } from "@/components/Logo";
import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

/**
 * Covers the app while Finvest is being updated. The client's money keeps earning, and the
 * screen goes away by itself as soon as Finvest answers again.
 */
export function MaintenanceScreen({ checking, onRetry }: { checking: boolean; onRetry: () => void }) {
  const insets = useSafeAreaInsets();
  return (
    <View
      style={[styles.page, { paddingTop: insets.top + 56, paddingBottom: insets.bottom + 28 }]}
      accessibilityViewIsModal
      testID="maintenance"
    >
      <View style={styles.column}>
        <LogoMark size={56} onDark />
        <View style={{ gap: 10 }}>
          <Txt accessibilityRole="header" size={28} weight="extrabold" lineHeight={34} color={colors.white} style={{ letterSpacing: -0.5 }}>
            We are updating Finvest
          </Txt>
          <Txt size={15} lineHeight={22} color={colors.onBlackMuted}>
            The app is paused for a short update. Your investments keep earning interest as usual, and nothing you started is lost.
          </Txt>
        </View>
        <View style={styles.card}>
          <Txt size={14} weight="bold" color={colors.white}>
            What still works
          </Txt>
          <Txt size={13} lineHeight={20} color={colors.onBlackMuted}>
            Interest is added every night, and investments that mature are paid or rolled over on time. Payments you already made are kept.
          </Txt>
        </View>
        <View style={{ flex: 1 }} />
        <Button label="Try again" variant="lime" loading={checking} onPress={onRetry} testID="maintenance-retry" />
        <Txt size={13} color={colors.onBlackMuted} style={{ textAlign: "center" }}>
          We check again by ourselves every 30 seconds.
        </Txt>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.black, paddingHorizontal: 24, alignItems: "center" },
  column: { flex: 1, width: "100%", maxWidth: 440, gap: 20 },
  card: { backgroundColor: colors.onBlackSurface, borderRadius: 20, padding: 16, gap: 6 },
});
