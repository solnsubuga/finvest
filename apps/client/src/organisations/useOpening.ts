import { useCallback } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Loaded, useFocusLoaded } from "@/investing/useLoaded";

import { clearIntent, readIntent } from "./intent";
import { intentFor } from "./intentTypes";
import { useOrganisations } from "./OrganisationsProvider";

/**
 * True when this client signed up from "Open an account for a company or group" and has not
 * applied for one yet. Once they have an application of their own, the choice is spent.
 */
export function useOpeningOrganisation(): Loaded<boolean> {
  const { session } = useAuth();
  const organisations = useOrganisations();
  const phone = session?.phone ?? null;
  const [opening] = useFocusLoaded(
    useCallback(async () => {
      if (!(await intentFor(await readIntent(), phone))) return false;
      const applied = (await organisations.list()).some((found) => !found.invited);
      if (applied) await clearIntent();
      return !applied;
    }, [organisations, phone]),
  );
  return opening;
}
