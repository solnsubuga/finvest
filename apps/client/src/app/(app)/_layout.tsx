import { Redirect, Tabs } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { destinations } from "@/navigation/destinations";
import { Sidebar } from "@/navigation/Sidebar";
import { usePushRegistration } from "@/push/usePushRegistration";
import { TabBar } from "@/navigation/TabBar";
import { colors } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

/**
 * The signed-in shell. One navigator, two faces: bottom tabs on a phone and a
 * left sidebar on a wide screen. Screens keep their state when the window is
 * resized across the breakpoint, because only the chrome changes.
 */
export default function AppLayout() {
  const { session } = useAuth();
  const wide = useIsWide();
  usePushRegistration();

  if (!session) {
    return <Redirect href="/welcome" />;
  }

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        sceneStyle: { backgroundColor: colors.paper },
        // On a wide screen the "tab bar" is the sidebar, so it sits on the left.
        tabBarPosition: wide ? "left" : "bottom",
      }}
      tabBar={({ state }) => {
        const activeName = state.routes[state.index]?.name ?? "index";
        return wide ? (
          <Sidebar activeName={activeName} session={session} />
        ) : (
          <TabBar activeName={activeName} />
        );
      }}
    >
      {destinations.map((destination) => (
        <Tabs.Screen
          key={destination.name}
          name={destination.name}
          options={{ title: destination.label }}
        />
      ))}
    </Tabs>
  );
}
