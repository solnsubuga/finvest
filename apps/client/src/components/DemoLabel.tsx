import { View } from "react-native";

import { useAuth } from "@/auth/AuthProvider";
import { colors, radii } from "@/theme";

import { Txt } from "./Txt";

/**
 * Says, on every screen, that the in-memory demo is answering and nothing
 * shown is real. Renders nothing once the app talks to the real API.
 */
export function DemoLabel({ onDark = false, signedIn = false }: { onDark?: boolean; signedIn?: boolean }) {
  const { demo, demoCode } = useAuth();
  if (!demo) {
    return null;
  }
  const text = signedIn
    ? "Demo. Not connected to Finvest yet, so nothing here is real."
    : `Demo sign-in, not connected to Finvest yet. Any SMS or authenticator code is ${demoCode}.`;
  return (
    <View
      style={{
        backgroundColor: onDark ? colors.onBlackSurface : colors.surfaceSunken,
        borderRadius: radii.sm,
        paddingVertical: 8,
        paddingHorizontal: 12,
      }}
    >
      <Txt size={12} weight="semibold" color={onDark ? colors.white : colors.ink}>
        {text}
      </Txt>
    </View>
  );
}
