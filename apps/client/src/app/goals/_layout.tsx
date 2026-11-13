import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

/** Setting up a goal, its detail and saving to it. Only for a signed-in client. */
export default function GoalsLayout() {
  const { session } = useAuth();
  if (!session) {
    return <Redirect href="/welcome" />;
  }
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.paper } }} />
  );
}
