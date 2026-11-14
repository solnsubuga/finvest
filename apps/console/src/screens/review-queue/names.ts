import { useEffect, useState } from "react";
import type { ClientCard, Directory } from "@/directory/api.ts";

/**
 * Names for the IDs a list holds. A name that cannot be found stays unknown: the screen says
 * "A client" or "A colleague", because a name is never what the screen is for.
 */
export function useNames(directory: Directory, clientIds: readonly string[], staffIds: readonly string[]) {
  const [clients, setClients] = useState<Record<string, ClientCard | null>>({});
  const [staff, setStaff] = useState<Record<string, string | null>>({});
  const clientKey = [...new Set(clientIds)].sort().join(",");
  const staffKey = [...new Set(staffIds)].sort().join(",");

  useEffect(() => {
    let current = true;
    for (const id of clientKey ? clientKey.split(",") : []) {
      void directory.client(id).then((card) => current && setClients((known) => ({ ...known, [id]: card })));
    }
    return () => {
      current = false;
    };
  }, [directory, clientKey]);

  useEffect(() => {
    let current = true;
    for (const id of staffKey ? staffKey.split(",") : []) {
      void directory.staffName(id).then((name) => current && setStaff((known) => ({ ...known, [id]: name })));
    }
    return () => {
      current = false;
    };
  }, [directory, staffKey]);

  return { clients, staff };
}
