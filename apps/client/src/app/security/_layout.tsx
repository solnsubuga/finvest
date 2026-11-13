import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

/** Sign in and security. Only for a signed-in client. */
export default function SecurityLayout() {
  const { session } = useAuth();
  if (!session) {
    return <Redirect href="/welcome" />;
  }
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.paper } }} />
  );
}
