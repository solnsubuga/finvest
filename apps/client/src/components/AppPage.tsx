import { ReactNode } from "react";
import { ScrollView, StyleSheet, View } from "react-native";
import { StatusBar } from "expo-status-bar";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, fontSizes, spacing } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

import { Notice } from "./Blocks";
import { DemoLabel } from "./DemoLabel";
import { Txt } from "./Txt";

type Props = {
  title: string;
  subtitle?: string;
  /** A control at the end of the heading row, such as the notifications bell. */
  action?: ReactNode;
  children?: ReactNode;
  /** Replaces the heading row, for a page with its own header such as Home on a phone. */
  header?: ReactNode;
  /** Lets a dashboard use more of a wide screen than a reading page does. */
  roomy?: boolean;
  testID?: string;
};

/** The frame for a signed-in page: heading, then content, with room for the wide layout. */
export function AppPage({ title, subtitle, action, children, header, roomy = false, testID }: Props) {
  const insets = useSafeAreaInsets();
  const wide = useIsWide();
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.paper }}
      contentContainerStyle={[
        styles.content,
        wide ? [styles.wide, roomy && { maxWidth: 1200 }] : { paddingTop: insets.top + spacing[6] },
      ]}
      testID={testID}
    >
      <StatusBar style="dark" />
      <DemoLabel signedIn />
      {header ?? (
      <View style={styles.heading}>
        <View style={{ flex: 1, gap: 4 }}>
          <Txt
            accessibilityRole="header"
            size={26}
            weight="extrabold"
            lineHeight={32}
            style={{ letterSpacing: -0.5 }}
          >
            {title}
          </Txt>
          {subtitle ? (
            <Txt size={14} color={colors.muted}>
              {subtitle}
            </Txt>
          ) : null}
        </View>
        {action}
      </View>
      )}
      {children}
    </ScrollView>
  );
}

/** Stands in for a page that a later phase builds. */
export function ComingLater({ title, what }: { title: string; what: string }) {
  return (
    <AppPage title={title}>
      <Notice tone="neutral">{`Nothing here yet. ${what}`}</Notice>
      <Txt size={fontSizes.sm} color={colors.muted}>
        This page is part of the app shell. Its content arrives in a later phase.
      </Txt>
    </AppPage>
  );
}

const styles = StyleSheet.create({
  content: { flexGrow: 1, paddingHorizontal: 20, paddingBottom: spacing[6], gap: spacing[4] },
  heading: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  wide: { paddingTop: 28, paddingHorizontal: 32, maxWidth: 960 },
});
