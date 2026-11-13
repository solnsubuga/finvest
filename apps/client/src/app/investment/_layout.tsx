import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

/** One of the client's own investments. Only for a signed-in client. */
export default function InvestmentLayout() {
  const { session } = useAuth();
  if (!session) {
    return <Redirect href="/welcome" />;
  }
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.paper } }} />
  );
}
