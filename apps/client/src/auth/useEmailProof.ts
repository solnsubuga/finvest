import { useCallback, useState } from "react";
import { useFocusEffect } from "expo-router";

import { useAuth } from "./AuthProvider";
import { describeAuthError } from "./types";

export type EmailProofNote = { tone: "neutral" | "danger"; text: string } | null;

/**
 * Whether the signed-in client still has to confirm their email address, and a way to send
 * another link. The account works either way: an unconfirmed address only means statements and
 * receipts are not emailed and the client cannot log in with it.
 *
 * A link opened in another tab or on another device confirms the address there, so the session is
 * read again whenever the screen using this comes into view.
 */
export function useEmailProof() {
  const { client, session, setSession } = useAuth();
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState<EmailProofNote>(null);

  useFocusEffect(
    useCallback(() => {
      let live = true;
      client
        .restoreSession()
        .then((fresh) => {
          if (live && fresh) setSession(fresh);
        })
        .catch(() => {
          // Offline: keep what is shown.
        });
      return () => {
        live = false;
      };
    }, [client, setSession]),
  );

  const pending = Boolean(session?.email && !session.emailVerified);

  const resend = useCallback(async () => {
    if (!session) return;
    setSending(true);
    setNote(null);
    try {
      const result = await client.sendEmailProof();
      if (result === "proved") {
        setSession({ ...session, emailVerified: true });
      } else {
        setNote({
          tone: "neutral",
          text: `We emailed a new link to ${session.email ?? "your address"}. Open it within 24 hours, on any device. Only the newest link works.`,
        });
      }
    } catch (caught) {
      setNote({ tone: "danger", text: describeAuthError(caught) });
    } finally {
      setSending(false);
    }
  }, [client, session, setSession]);

  return { email: session?.email ?? null, pending, proved: Boolean(session?.emailVerified), resend, sending, note };
}
