import { maskE164 } from "./phone";
import { CodeChannel, LoginChallenge } from "./types";

/** "a••••@example.com", for screens that confirm where a code was sent. */
export function maskEmail(email: string): string {
  const [name = "", domain = ""] = email.trim().split("@");
  return `${name.slice(0, 1)}••••@${domain}`;
}

/** Where a code for this log in goes, and how to say so. */
export type CodeTarget = { channel: CodeChannel; where: string };

/**
 * A log in by email sends the code to that proved address, which is how a
 * client who lost their phone gets in. A log in by number texts it.
 */
export function codeTargetFor(login: LoginChallenge, channel?: CodeChannel): CodeTarget {
  if (login.email && (channel ?? "email") === "email") return { channel: "email", where: maskEmail(login.email) };
  return { channel: "sms", where: login.phone ? maskE164(login.phone) : "the number on your account" };
}
