import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

/** The identity check. Only for a signed-in client. */
export default function IdCheckLayout() {
  const { session } = useAuth();
  if (!session) {
    return <Redirect href="/welcome" />;
  }
  return <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.paper } }} />;
}
