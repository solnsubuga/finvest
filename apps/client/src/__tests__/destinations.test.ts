import { readdirSync } from "fs";
import { join } from "path";

import { destinations, SIDEBAR_ACCOUNT_DESTINATION, sidebarGroups, tabBarDestinations } from "@/navigation/destinations";

describe("destinations", () => {
  it("has a route file for every destination and a destination for every route file", () => {
    const files = readdirSync(join(__dirname, "..", "app", "(app)"))
      .filter((file) => file.endsWith(".tsx") && !file.startsWith("_"))
      .map((file) => file.replace(/\.tsx$/, ""))
      .sort();
    expect(destinations.map((destination) => destination.name).sort()).toEqual(files);
  });

  it("puts Home, Invest, Goals, Activity and Profile in the phone tab bar", () => {
    expect(tabBarDestinations.map((destination) => destination.label)).toEqual([
      "Home",
      "Invest",
      "Goals",
      "Activity",
      "Profile",
    ]);
  });

  it("has no wallet destination", () => {
    expect(destinations.some((destination) => /wallet|balance/i.test(destination.label))).toBe(
      false,
    );
  });

  it("groups every destination in the sidebar exactly once, with Profile behind the signed-in name", () => {
    const grouped = [
      ...sidebarGroups.flatMap((group) => group.destinations.map((d) => d.name)),
      SIDEBAR_ACCOUNT_DESTINATION,
    ];
    expect([...grouped].sort()).toEqual(destinations.map((d) => d.name).sort());
    expect(new Set(grouped).size).toBe(grouped.length);
  });

  it("puts Home alone at the top, then Money, Planning and Account", () => {
    expect(
      sidebarGroups.map((group) => [group.heading, group.destinations.map((d) => d.label)]),
    ).toEqual([
      [null, ["Home"]],
      ["Money", ["Invest", "Portfolio", "Activity"]],
      ["Planning", ["Goals", "Insights"]],
      ["Account", ["Documents", "Help"]],
    ]);
  });
});
