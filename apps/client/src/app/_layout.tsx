import { useEffect } from "react";
import { Stack } from "expo-router";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider } from "@/auth/AuthProvider";
import { AutoinvestProvider } from "@/autoinvest/AutoinvestProvider";
import { GoalsProvider } from "@/goals/GoalsProvider";
import { InsightsProvider } from "@/insights/InsightsProvider";
import { PaymentsProvider } from "@/payments/PaymentsProvider";
import { SupportProvider } from "@/support/SupportProvider";
import { colors, fontAssets } from "@/theme";

void SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [loaded, error] = useFonts(fontAssets);

  useEffect(() => {
    if (loaded || error) {
      void SplashScreen.hideAsync();
    }
  }, [loaded, error]);

  // If the font fails to load the app still opens, in the system font.
  if (!loaded && !error) {
    return null;
  }

  return (
    <SafeAreaProvider>
      <AuthProvider>
        <PaymentsProvider>
          <SupportProvider>
            <AutoinvestProvider>
              <InsightsProvider>
                <GoalsProvider>
                  <Stack
                    screenOptions={{
                      headerShown: false,
                      contentStyle: { backgroundColor: colors.paper },
                    }}
                  />
                </GoalsProvider>
              </InsightsProvider>
            </AutoinvestProvider>
          </SupportProvider>
        </PaymentsProvider>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
