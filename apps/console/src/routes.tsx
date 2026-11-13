import type { ReactNode } from "react";
import { Navigate, type RouteObject } from "react-router";
import { useAuth } from "@/auth/context.ts";
import { can } from "@/auth/types.ts";
import { AppShell } from "@/components/AppShell.tsx";
import { homePath, NAV_ITEMS, type NavItem } from "@/nav/nav.ts";
import { EnrolPage } from "@/pages/EnrolPage.tsx";
import { NoAccessPage } from "@/pages/NoAccessPage.tsx";
import { NotFoundPage } from "@/pages/NotFoundPage.tsx";
import { PlaceholderPage } from "@/pages/PlaceholderPage.tsx";
import { SCREENS } from "@/screens/registry.ts";
import { SignInPage } from "@/pages/SignInPage.tsx";

/** Hiding a menu item is not enough: typing the address must not open the screen either. */
function Guarded({ item, children }: { item: NavItem; children: ReactNode }) {
  const { session } = useAuth();
  if (!session || !can(session, item.permission)) return <NoAccessPage />;
  return children;
}

/** The built screen for a menu item, or the placeholder until there is one. */
function screenFor(item: NavItem): ReactNode {
  const Screen = SCREENS.get(item.path);
  return Screen ? <Screen /> : <PlaceholderPage item={item} />;
}

function Home() {
  const { session } = useAuth();
  const home = session ? homePath(session) : null;
  return home ? <Navigate to={home} replace /> : <NoAccessPage />;
}

export const routes: RouteObject[] = [
  { path: "/sign-in", element: <SignInPage /> },
  { path: "/enrol", element: <EnrolPage /> },
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Home /> },
      ...NAV_ITEMS.map((item) => ({
        path: item.path,
        element: <Guarded item={item}>{screenFor(item)}</Guarded>,
      })),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
