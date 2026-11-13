import { Pressable, StyleSheet, View } from "react-native";

import { colors } from "@/theme";

import { Icon, IconName } from "./Icon";
import { Txt } from "./Txt";

type Props = {
  icon: IconName;
  title: string;
  description: string;
  onPress: () => void;
  testID?: string;
};

/** A white row that leads somewhere: icon tile, title and one line, chevron. */
export function LinkCard({ icon, title, description, onPress, testID }: Props) {
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={title}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}
    >
      <View style={styles.tile}>
        <Icon name={icon} color={colors.lime} />
      </View>
      <View style={{ flex: 1 }}>
        <Txt weight="bold">{title}</Txt>
        <Txt size={13} color={colors.muted}>
          {description}
        </Txt>
      </View>
      <Icon name="chevron" size={16} strokeWidth={2.2} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 14,
  },
  tile: {
    width: 44,
    height: 44,
    borderRadius: 14,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
});
