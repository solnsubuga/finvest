import { View } from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

const topics: Record<string, { title: string; body: string }> = {
  "locked-out": {
    title: "Get back into your account",
    body: "Account recovery in the app is not ready yet.",
  },
  group: {
    title: "Company and group accounts",
    body: "Opening an account for a company, SACCO or group in the app is not ready yet.",
  },
};

const fallback = { title: "Not ready yet", body: "This part of the app is not ready yet." };

/** Where links lead when their screen belongs to a later phase. No dead ends. */
export default function Unavailable() {
  const { topic } = useLocalSearchParams<{ topic?: string }>();
  const copy = (topic ? topics[topic] : undefined) ?? fallback;
  return (
    <Screen>
      <Stack.Screen options={{ title: copy.title }} />
      <BackButton fallback="/welcome" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>{copy.title}</Title>
        <Txt color={colors.muted}>{copy.body}</Txt>
      </View>
      <View style={{ marginTop: 20 }}>
        <Notice tone="neutral">
          This screen is on the design and will be built in a later phase.
        </Notice>
      </View>
    </Screen>
  );
}
