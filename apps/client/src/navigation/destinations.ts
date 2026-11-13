import { Href } from "expo-router";

import { IconName } from "@/components/Icon";

export type Destination = {
  /** The route file name inside `src/app/(app)`. */
  name: string;
  href: Href;
  label: string;
  icon: IconName;
  /** Shown in the bottom tab bar on a phone. The sidebar shows every destination. */
  inTabBar: boolean;
};

/**
 * One list drives both navigators, so the phone tabs and the wide sidebar can
 * never disagree. The order is the sidebar order on the design canvas.
 */
export const destinations: Destination[] = [
  { name: "index", href: "/", label: "Home", icon: "home", inTabBar: true },
  {
    name: "invest",
    href: "/invest",
    label: "Invest",
    icon: "invest",
    inTabBar: true,
  },
  {
    name: "portfolio",
    href: "/portfolio",
    label: "Portfolio",
    icon: "portfolio",
    inTabBar: false,
  },
  {
    name: "insights",
    href: "/insights",
    label: "Insights",
    icon: "insights",
    inTabBar: false,
  },
  {
    name: "goals",
    href: "/goals",
    label: "Goals",
    icon: "goals",
    inTabBar: true,
  },
  {
    name: "activity",
    href: "/activity",
    label: "Activity",
    icon: "activity",
    inTabBar: true,
  },
  {
    name: "documents",
    href: "/documents",
    label: "Documents",
    icon: "documents",
    inTabBar: false,
  },
  {
    name: "profile",
    href: "/profile",
    label: "Profile",
    icon: "profile",
    inTabBar: false,
  },
  { name: "help", href: "/help", label: "Help", icon: "help", inTabBar: false },
];

export const tabBarDestinations = destinations.filter(
  (destination) => destination.inTabBar,
);
