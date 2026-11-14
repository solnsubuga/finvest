import { expect, type Page } from "@playwright/test";

// A PIN the app accepts: not repeated digits and not a run. Demo value only.
export const DEMO_PIN = "48151";

/** Types a PIN on the app's own keypad, a key at a time, as a client does. */
export async function typePin(page: Page, pin: string) {
  for (const digit of pin) {
    await page.getByTestId(`pin-key-${digit}`).filter({ visible: true }).click();
  }
}

/** The PIN the app asks for after each log in on a phone (and in the demo): typed, then again. */
export async function createPin(page: Page, pin = DEMO_PIN) {
  await expect(page.getByText("Create a 5 digit PIN")).toBeVisible();
  await typePin(page, pin);
  await expect(page.getByText("Type your PIN again")).toBeVisible();
  await typePin(page, pin);
}

/**
 * Puts the app in the background for longer than the lock allows, and brings it back. Needs
 * `page.clock.install()` before the page loads.
 */
export async function leaveForSixMinutes(page: Page) {
  const show = (state: "hidden" | "visible") =>
    page.evaluate((value) => {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => value });
      document.dispatchEvent(new Event("visibilitychange"));
    }, state);
  await show("hidden");
  await page.clock.fastForward("06:00");
  await show("visible");
}
