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

export type SidebarGroup = {
  key: string;
  /** Shown above the group. Home stands alone at the top with no heading. */
  heading: string | null;
  destinations: Destination[];
};

/**
 * One list drives both navigators, so the phone tabs and the wide sidebar can
 * never disagree. This order is the phone tab bar order; the sidebar groups
 * the same destinations under headings (see `sidebarGroups`).
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

export const tabBarDestinations = destinations.filter((destination) => destination.inTabBar);

/**
 * The wide sidebar's sections, in order. Home stands alone, then the money the
 * client has and moves, then what they plan, then their account and help. The
 * phone keeps the same split: four tabs, with Portfolio and Insights reached
 * from Home and Documents and Help from Profile.
 */
const sidebarLayout: {
  key: string;
  heading: string | null;
  names: string[];
}[] = [
  { key: "home", heading: null, names: ["index"] },
  {
    key: "money",
    heading: "Money",
    names: ["invest", "portfolio", "activity"],
  },
  { key: "planning", heading: "Planning", names: ["goals", "insights"] },
  {
    key: "account",
    heading: "Account",
    names: ["profile", "documents", "help"],
  },
];

export const sidebarGroups: SidebarGroup[] = sidebarLayout.map((group) => ({
  key: group.key,
  heading: group.heading,
  destinations: group.names.map((name) => {
    const destination = destinations.find((candidate) => candidate.name === name);
    if (!destination) {
      throw new Error(`The sidebar names an unknown destination: ${name}`);
    }
    return destination;
  }),
}));
