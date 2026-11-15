import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

/**
 * An address the app has no screen for: an old link, a typo, or a page that has moved. One way
 * back, to Home when signed in and to the start otherwise. Nothing from the address is shown.
 */
export default function NotFound() {
  const router = useRouter();
  const { session } = useAuth();
  const signedIn = Boolean(session);
  return (
    <Screen
      signedIn={signedIn}
      footer={
        <Button
          label={signedIn ? "Go to Home" : "Go to the start"}
          onPress={() => router.replace(signedIn ? "/" : "/welcome")}
          testID="not-found-home"
        />
      }
    >
      <Stack.Screen options={{ title: "Page not found" }} />
      <View style={{ marginTop: 48, gap: 10 }}>
        <Title>We could not find that page</Title>
        <Txt color={colors.muted}>
          The link may be old or mistyped, or the page has moved. Your money and account are not
          affected.
        </Txt>
      </View>
    </Screen>
  );
}
