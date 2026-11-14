import { readdirSync } from "fs";
import { join } from "path";

import { destinations, sidebarGroups, tabBarDestinations } from "@/navigation/destinations";

describe("destinations", () => {
  it("has a route file for every destination and a destination for every route file", () => {
    const files = readdirSync(join(__dirname, "..", "app", "(app)"))
      .filter((file) => file.endsWith(".tsx") && !file.startsWith("_"))
      .map((file) => file.replace(/\.tsx$/, ""))
      .sort();
    expect(destinations.map((destination) => destination.name).sort()).toEqual(files);
  });

  it("puts Home, Invest, Goals and Activity in the phone tab bar", () => {
    expect(tabBarDestinations.map((destination) => destination.label)).toEqual([
      "Home",
      "Invest",
      "Goals",
      "Activity",
    ]);
  });

  it("has no wallet destination", () => {
    expect(destinations.some((destination) => /wallet|balance/i.test(destination.label))).toBe(
      false,
    );
  });

  it("groups every destination in the sidebar exactly once", () => {
    const grouped = sidebarGroups.flatMap((group) => group.destinations.map((d) => d.name));
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
      ["Account", ["Profile", "Documents", "Help"]],
    ]);
  });
});
