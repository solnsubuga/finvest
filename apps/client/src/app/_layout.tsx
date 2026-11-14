import { useEffect } from "react";
import { Stack } from "expo-router";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ActivityProvider } from "@/activity/ActivityProvider";
import { AuthProvider } from "@/auth/AuthProvider";
import { AutoinvestProvider } from "@/autoinvest/AutoinvestProvider";
import { ClaimProvider } from "@/claim/ClaimProvider";
import { DataSavingProvider } from "@/dataSaving/DataSavingProvider";
import { DocumentsProvider } from "@/documents/DocumentsProvider";
import { GoalsProvider } from "@/goals/GoalsProvider";
import { IdentityProvider } from "@/identity/IdentityProvider";
import { InsightsProvider } from "@/insights/InsightsProvider";
import { InvestingProvider } from "@/investing/InvestingProvider";
import { PaymentsProvider } from "@/payments/PaymentsProvider";
import { PayoutsProvider } from "@/payouts/PayoutsProvider";
import { OrganisationsProvider } from "@/organisations/OrganisationsProvider";
import { ProfileProvider } from "@/profile/ProfileProvider";
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
      <DataSavingProvider>
        <AuthProvider>
          <IdentityProvider>
            <ClaimProvider>
              <PaymentsProvider>
                <SupportProvider>
                  <InvestingProvider>
                    <AutoinvestProvider>
                      <InsightsProvider>
                        <GoalsProvider>
                          <DocumentsProvider>
                            <PayoutsProvider>
                              <ActivityProvider>
                                <ProfileProvider>
                                  <OrganisationsProvider>
                                    <Stack
                                      screenOptions={{
                                        headerShown: false,
                                        contentStyle: { backgroundColor: colors.paper },
                                      }}
                                    />
                                  </OrganisationsProvider>
                                </ProfileProvider>
                              </ActivityProvider>
                            </PayoutsProvider>
                          </DocumentsProvider>
                        </GoalsProvider>
                      </InsightsProvider>
                    </AutoinvestProvider>
                  </InvestingProvider>
                </SupportProvider>
              </PaymentsProvider>
            </ClaimProvider>
          </IdentityProvider>
        </AuthProvider>
      </DataSavingProvider>
    </SafeAreaProvider>
  );
}
