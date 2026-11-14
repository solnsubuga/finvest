import { useEffect } from "react";
import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

/** The steps straight after signing up or logging in: the PIN, and the offer of a passkey. */
export default function SetupLayout() {
  const { session, returnTo, setReturnTo } = useAuth();

  // The auth layout brought the client here, so the note is spent.
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
