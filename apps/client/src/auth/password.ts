import { nationalDigits } from "./phone";

export const MIN_PASSWORD_LENGTH = 10;

export type PasswordChecks = {
  longEnough: boolean;
  /** The password does not contain the client's own mobile number. */
  notPersonal: boolean;
  matches: boolean;
};

/**
 * The checks the app can make on its own. The leaked password check needs the
 * server, so it happens when the form is sent and is reported as an error.
 */
export function checkPassword(
  password: string,
  confirmation: string,
  phone: string,
): PasswordChecks {
  const digits = nationalDigits(phone);
  const passwordDigits = password.replace(/\D/g, "");
  return {
    longEnough: password.length >= MIN_PASSWORD_LENGTH,
    notPersonal:
      password.length > 0 &&
      (digits.length < 6 || !passwordDigits.includes(digits.slice(-6))),
    matches: password.length > 0 && password === confirmation,
  };
}

export function passwordIsAcceptable(checks: PasswordChecks): boolean {
  return checks.longEnough && checks.notPersonal && checks.matches;
}

export type PasswordStrength = { label: string; fraction: number; ok: boolean };

/** A rough guide for the meter. Length is what matters most, so it leads. */
export function passwordStrength(password: string): PasswordStrength {
  if (password.length === 0) {
    return { label: "", fraction: 0, ok: false };
  }
  if (password.length < MIN_PASSWORD_LENGTH) {
    return { label: "Too short", fraction: 0.25, ok: false };
  }
  if (password.length < 14) {
    return { label: "Good password", fraction: 0.6, ok: true };
  }
  return { label: "Strong password", fraction: 0.85, ok: true };
}

export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}
