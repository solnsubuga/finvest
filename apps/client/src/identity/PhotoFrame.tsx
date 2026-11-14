import { StyleSheet, View } from "react-native";

import { colors } from "@/theme";

/** Where the photo goes: a card with lime corners, or an oval for a face. */
export function Frame({ selfie }: { selfie: boolean }) {
  return (
    <View style={[styles.frame, selfie && { height: 260 }]} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <View style={[styles.corner, { top: -2, left: -2, borderTopWidth: 4, borderLeftWidth: 4, borderTopLeftRadius: 18 }]} />
      <View style={[styles.corner, { top: -2, right: -2, borderTopWidth: 4, borderRightWidth: 4, borderTopRightRadius: 18 }]} />
      <View style={[styles.corner, { bottom: -2, left: -2, borderBottomWidth: 4, borderLeftWidth: 4, borderBottomLeftRadius: 18 }]} />
      <View style={[styles.corner, { bottom: -2, right: -2, borderBottomWidth: 4, borderRightWidth: 4, borderBottomRightRadius: 18 }]} />
      {selfie ? (
        <View style={styles.face} />
      ) : (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 16, width: "100%" }}>
          <View style={styles.portrait} />
          <View style={{ flex: 1, gap: 12 }}>
            {[90, 70, 80, 50].map((width) => (
              <View key={width} style={[styles.textLine, { width: `${width}%` }]} />
            ))}
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    height: 200,
    borderRadius: 18,
    backgroundColor: colors.ink,
    padding: 22,
    alignItems: "center",
    justifyContent: "center",
  },
  corner: { position: "absolute", width: 34, height: 34, borderColor: colors.lime },
  portrait: { width: 80, height: 100, borderRadius: 10, borderWidth: 2, borderStyle: "dashed", borderColor: colors.neutral },
  textLine: { height: 8, borderRadius: 4, backgroundColor: colors.onBlackDivider },
  face: { width: 150, height: 196, borderRadius: 98, borderWidth: 3, borderStyle: "dashed", borderColor: colors.neutral },
});
