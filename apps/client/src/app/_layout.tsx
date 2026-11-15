import { useEffect } from "react";
import { Stack } from "expo-router";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ActivityProvider } from "@/activity/ActivityProvider";
import { AuthProvider } from "@/auth/AuthProvider";
import { AutoinvestProvider } from "@/autoinvest/AutoinvestProvider";
import { ClaimProvider } from "@/claim/ClaimProvider";
import { ConnectionProvider } from "@/connection/ConnectionProvider";
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
import { RoutePrefetch } from "@/navigation/RoutePrefetch";
import { hideStartLoader } from "@/navigation/startLoader";
import { START_LOADER_LIMIT_MS } from "@/navigation/useStartLoaderDone";
import { colors, fontAssets } from "@/theme";

void SplashScreen.preventAutoHideAsync();

// A screen that fails to load or draw shows a page that says so and tries again, never a blank
// page. On the web that covers a screen opened for the first time while offline.
export { ScreenLoadFailed as ErrorBoundary } from "@/navigation/ScreenLoadFailed";

export default function RootLayout() {
  const [loaded, error] = useFonts(fontAssets);
  // If the font fails to load the app still opens, in the system font.
  const fontsReady = loaded || Boolean(error);

  useEffect(() => {
    if (!fontsReady) {
      return undefined;
    }
    void SplashScreen.hideAsync();
    // The first screen removes the web page's start loader (useStartLoaderDone). This is the
    // backstop for a screen drawn some other way.
    const timer = setTimeout(hideStartLoader, START_LOADER_LIMIT_MS);
    return () => clearTimeout(timer);
  }, [fontsReady]);

  // The providers start at once, so the session is looked up while the font is still loading,
  // and only the screens wait for the font. The splash, or the web page's loader, covers both.
  return (
    <SafeAreaProvider>
      <DataSavingProvider>
        <RoutePrefetch />
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
                                    <ConnectionProvider>
                                      {fontsReady ? (
                                        <Stack
                                          screenOptions={{
                                            headerShown: false,
                                            contentStyle: { backgroundColor: colors.paper },
                                          }}
                                        />
                                      ) : null}
                                    </ConnectionProvider>
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
