import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

/** "Powered by Regula", with what the check is and what Regula gets behind a tap. */
export function PoweredByRegula() {
  const [open, setOpen] = useState(false);
  return (
    <View style={{ gap: 8, alignItems: "flex-start" }}>
      <Pressable
        onPress={() => setOpen((shown) => !shown)}
        accessibilityRole="button"
        accessibilityLabel="Powered by Regula. About this check"
        accessibilityState={{ expanded: open }}
        hitSlop={8}
        style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}
        testID="powered-by-regula"
      >
        <Txt color={colors.muted}>Powered by Regula</Txt>
        <Icon name="help" size={16} color={colors.muted} />
      </Pressable>
      {open ? (
        <View style={styles.tip} accessibilityLiveRegion="polite" testID="powered-by-regula-tip">
          <Txt size={fontSizes.sm} color={colors.white}>
            A short check by our identity partner, right here in Finvest. You look at the camera and move as it asks, so
            we know a real person is in front of it.
          </Txt>
          <Txt size={fontSizes.sm} color={colors.white}>
            They take the picture and get no name or number from us. Finvest uses it only to confirm it is you.
          </Txt>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  tip: { backgroundColor: colors.black, borderRadius: 14, padding: 14, gap: 8, alignSelf: "stretch" },
});
