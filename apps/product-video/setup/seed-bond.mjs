// Off camera, a week before the story starts: Joel and Ruth add the 5 year bond Esther later
// buys, and open it to clients, so the website and the app show bills and bonds from day one.
// The same steps as chapter 6, with nothing filmed.
import { closeBrowser } from "../lib/session.mjs";
import { BOND, proposeBond, checkBond, openBond } from "../chapters/06-bond.mjs";
import { staffPage } from "./staff-login.mjs";

/** A take that films nothing: it only clicks and types. */
const offCamera = {
  start: async () => {},
  stop: async () => {},
  step: async () => {},
  read: async () => {},
  wait: async (ms) => new Promise((r) => setTimeout(r, ms)),
  zoom: async () => {},
  hover: async () => {},
  scroll: async () => {},
  click: async (locator) => locator.click(),
  type: async (locator, text) => locator.fill(text),
};

const joel = await staffPage("joel");
const ruth = await staffPage("ruth");
await proposeBond(offCamera, joel.page, BOND);
await checkBond(offCamera, ruth.page, BOND);
await openBond(offCamera, joel.page, BOND);
console.log("bond open", BOND.code);
await joel.close();
await ruth.close();
await closeBrowser();
