import { ReactNode } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";

import { colors, COLUMN_WIDTH, spacing } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

import { DemoLabel } from "./DemoLabel";

type Props = {
  children: ReactNode;
  /** Pinned under the content: the main button and any links below it. */
  footer?: ReactNode;
  dark?: boolean;
  /** For screens reached after signing in, where the demo's sign-in hint would be out of place. */
  signedIn?: boolean;
};

/**
 * The frame for sign-up, log-in and other single-column screens.
 *
 * On a phone it fills the screen. On a wide screen the same column sits in
 * the centre, which is how every screen without its own wide layout behaves.
 */
export function Screen({ children, footer, dark = false, signedIn = false }: Props) {
  const insets = useSafeAreaInsets();
  const wide = useIsWide();
  const background = dark ? colors.black : colors.paper;

  return (
    <KeyboardAvoidingView
      style={[styles.root, { backgroundColor: background }]}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <StatusBar style={dark ? "light" : "dark"} />
      <ScrollView
        style={styles.root}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: insets.top + (wide ? 48 : spacing[6]),
            paddingBottom: insets.bottom + spacing[6],
          },
        ]}
      >
        <View style={styles.column}>
          <View>
            <View style={styles.demo}>
              <DemoLabel onDark={dark} signedIn={signedIn} />
            </View>
            {children}
          </View>
          {footer ? <View style={styles.footer}>{footer}</View> : null}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { flexGrow: 1, alignItems: "center", paddingHorizontal: spacing[6] },
  column: {
    flexGrow: 1,
    width: "100%",
    maxWidth: COLUMN_WIDTH,
    justifyContent: "space-between",
    gap: spacing[6],
  },
  footer: { gap: 6 },
  demo: { marginBottom: 16 },
});
