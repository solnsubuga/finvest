/**
 * Someone who chose "Open an account for a company or group" on the sign-up screen first opens
 * their own personal account: they are the applicant, so their own ID check must be approved
 * before the company or group application can start (Solo's rule: sign-up and KYB first).
 * That can take days, so the choice is kept on this device, tied to the mobile number they
 * signed up with, and Home leads them on to the application once they can make it.
 */
export type OrganisationIntent = { phone: string };

export function parseIntent(raw: string | null): OrganisationIntent | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object" && typeof (value as { phone?: unknown }).phone === "string") {
      return { phone: (value as { phone: string }).phone };
    }
  } catch {
    // A damaged entry is the same as none.
  }
  return null;
}

/** Only the person who made the choice sees it, not someone else who later signs in on this device. */
export function intentFor(intent: OrganisationIntent | null, phone: string | null | undefined): boolean {
  return intent !== null && !!phone && intent.phone === phone;
}
