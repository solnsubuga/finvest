import { ActivityIndicator, View } from "react-native";

import { colors } from "@/theme";

import { Notice } from "./Blocks";
import { TextLink } from "./Button";

/** A spinner with a name, for a list or a page that is being read. */
export function Loading({ label }: { label: string }) {
  return (
    <View style={{ paddingVertical: 32, alignItems: "center" }} accessibilityLabel={label}>
      <ActivityIndicator color={colors.black} />
    </View>
  );
}

/** What could not be read, and a way to try again. */
export function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={{ gap: 4, alignItems: "flex-start" }}>
      <Notice tone="danger">{message}</Notice>
      <TextLink label="Try again" onPress={onRetry} />
    </View>
  );
}
