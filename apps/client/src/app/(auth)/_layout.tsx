import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

export default function AuthLayout() {
  const { session, returnTo } = useAuth();
  if (session) {
    // A set-up that asked for a fresh log in gets the client back.
    return <Redirect href={returnTo ?? "/"} />;
  }
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.paper } }} />
  );
}
