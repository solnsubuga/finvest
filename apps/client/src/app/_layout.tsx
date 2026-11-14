import { useEffect } from "react";
import { Stack } from "expo-router";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider } from "@/auth/AuthProvider";
import { AutoinvestProvider } from "@/autoinvest/AutoinvestProvider";
import { DocumentsProvider } from "@/documents/DocumentsProvider";
import { GoalsProvider } from "@/goals/GoalsProvider";
import { IdentityProvider } from "@/identity/IdentityProvider";
import { InsightsProvider } from "@/insights/InsightsProvider";
import { InvestingProvider } from "@/investing/InvestingProvider";
import { PaymentsProvider } from "@/payments/PaymentsProvider";
import { PayoutsProvider } from "@/payouts/PayoutsProvider";
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
        <IdentityProvider>
          <PaymentsProvider>
            <SupportProvider>
              <InvestingProvider>
                <AutoinvestProvider>
                  <InsightsProvider>
                    <GoalsProvider>
                      <DocumentsProvider>
                        <PayoutsProvider>
                          <Stack
                            screenOptions={{
                              headerShown: false,
                              contentStyle: { backgroundColor: colors.paper },
                            }}
                          />
                        </PayoutsProvider>
                      </DocumentsProvider>
                    </GoalsProvider>
                  </InsightsProvider>
                </AutoinvestProvider>
              </InvestingProvider>
            </SupportProvider>
          </PaymentsProvider>
        </IdentityProvider>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
