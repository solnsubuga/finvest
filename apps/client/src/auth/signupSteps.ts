import { passkeysSupported } from "./webauthn";

export type SignupStep = "code" | "password" | "pin" | "passkey";

/**
 * The sign-up steps on this device, in order: prove the number, choose a
 * password, then a PIN on the phone app and the offer of a passkey where the
 * device can make one.
 */
export function signupSteps(pinApplies: boolean, canMakePasskey: boolean = passkeysSupported()): SignupStep[] {
  const steps: SignupStep[] = ["code", "password"];
  if (pinApplies) steps.push("pin");
  if (canMakePasskey) steps.push("passkey");
  return steps;
}

/** "Step n of m" for one step, counted from 1. */
export function stepOf(step: SignupStep, pinApplies: boolean): { step: number; of: number } {
  const steps = signupSteps(pinApplies);
  return { step: steps.indexOf(step) + 1, of: steps.length };
}
