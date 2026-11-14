import { useEffect } from "react";
import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/theme";

/** Next of kin, the investing questions, language and data, and closing the account. Signed in only. */
export default function AccountLayout() {
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
