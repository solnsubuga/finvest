import { useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { describeAuthError } from "@/auth/types";

export type EmailNote = { tone: "neutral" | "danger"; text: string };

/** Emails a link that proves the address, for Profile on a phone and on a wide screen. */
export function useEmailProof(): { sendLink: () => Promise<void>; sending: boolean; note: EmailNote | null } {
  const { client, session, setSession } = useAuth();
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState<EmailNote | null>(null);

  const sendLink = async () => {
    if (!session) {
      return;
    }
    setSending(true);
    setNote(null);
    try {
      const result = await client.sendEmailProof();
      if (result === "proved") {
        setSession({ ...session, emailVerified: true });
      } else {
        setNote({
          tone: "neutral",
          text: `We emailed a link to ${session.email ?? "your address"}. Open it within 24 hours, on any device. Only the newest link works.`,
        });
      }
    } catch (caught) {
      setNote({ tone: "danger", text: describeAuthError(caught) });
    } finally {
      setSending(false);
    }
  };

  return { sendLink, sending, note };
}
