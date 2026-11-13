import { useEffect } from "react";
import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

/** Sign in and security. Only for a signed-in client. */
export default function SecurityLayout() {
  const { session, returnTo, setReturnTo } = useAuth();

  // Once the client is back here after logging in again, the note is spent.
  useEffect(() => {
    if (session && returnTo) {
      setReturnTo(null);
    }
  }, [session, returnTo, setReturnTo]);

  if (!session) {
    return <Redirect href="/welcome" />;
  }
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.paper } }} />
  );
}
