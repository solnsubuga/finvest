import { Platform } from "react-native";

/** The app PIN, as the API checks it (backend/app/modules/identity/domain/pins.py). */
export const PIN_LENGTH = 5;
/** Wrong PINs in a row before this phone is signed out. */
export const MAX_WRONG_PINS = 5;
/** After this long in the background, the app asks for the PIN again, as the API does. */
export const LOCK_AFTER_MS = 5 * 60 * 1000;

export type PinProblem = "format" | "too_simple";

/** Why a new PIN would be refused, or null. The same rule as the API's. */
export function pinProblem(pin: string): PinProblem | null {
  if (!/^[0-9]{5}$/.test(pin)) return "format";
  const digits = [...pin].map(Number);
  const steps = new Set(digits.slice(1).map((digit, index) => digit - digits[index]!));
  if (steps.size === 1 && [0, 1, -1].includes([...steps][0]!)) return "too_simple";
  return null;
}

/**
 * A PIN belongs to the phone app. A browser is often shared and has none, so
 * the web never asks for one. The demo build shows it on the web too, so the
 * screens can be seen there; the demo answers in memory and nothing is kept.
 */
export function appPinApplies(demo: boolean): boolean {
  return Platform.OS !== "web" || demo;
}
