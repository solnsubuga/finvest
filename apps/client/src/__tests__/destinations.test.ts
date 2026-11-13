import { readdirSync } from "fs";
import { join } from "path";

import { destinations, tabBarDestinations } from "@/navigation/destinations";

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
});
