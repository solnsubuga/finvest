import { OrganisationIntent, parseIntent, storedIntent } from "./intentTypes";

/** In a browser the choice sits in this browser's storage. A private window may refuse it.
 * Only a digest of the number is kept, never the number. */
const KEY = "finvest.opening-organisation";

export async function readIntent(): Promise<OrganisationIntent | null> {
  try {
    return parseIntent(window.localStorage.getItem(KEY));
  } catch {
    return null;
  }
}

export async function writeIntent(phone: string): Promise<void> {
  try {
    window.localStorage.setItem(KEY, await storedIntent(phone));
  } catch {
    // Without it Profile, Company and group accounts still opens the application.
  }
}

export async function clearIntent(): Promise<void> {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing depends on it being gone: Home stops asking once an application exists.
  }
}
