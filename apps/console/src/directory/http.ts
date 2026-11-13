/**
 * The Directory over the real API.
 *
 * Staff names come from `GET /staff/people`, which every role may read. Clients come from
 * the Clients screen's routes, which need `clients.view`; every role holds it. A lookup that
 * fails answers "unknown" rather than an error, as a name is never what a screen is for.
 */

import type { Http } from "@/api/http.ts";
import type { ClientCard, Directory, StaffCard } from "./api.ts";

interface PeopleJson {
  people: { id: string; name: string }[];
}

interface AccountJson {
  id: string;
  created_at: string;
}

interface ClientPageJson {
  account: AccountJson;
  verified_name: string | null;
  identity_check: { status: string; account_number: string | null } | null;
}

/** A Kampala date, `YYYY-MM-DD`. */
function kampalaDay(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date(iso));
}

export function toClientCard(json: ClientPageJson): ClientCard {
  const passed = json.identity_check?.status === "approved";
  return {
    clientId: json.account.id,
    name: json.verified_name ?? "Name not yet verified",
    accountNumber: json.identity_check?.account_number ?? null,
    verified: passed,
    clientSince: kampalaDay(json.account.created_at),
  };
}

export function createHttpDirectory(http: Http): Directory {
  const names = new Map<string, Promise<string | null>>();
  const clients = new Map<string, Promise<ClientCard | null>>();

  const client = (clientId: string): Promise<ClientCard | null> => {
    let known = clients.get(clientId);
    if (!known) {
      known = http.request<ClientPageJson>("GET", `/staff/clients/${encodeURIComponent(clientId)}`).then(toClientCard, () => {
        clients.delete(clientId);
        return null;
      });
      clients.set(clientId, known);
    }
    return known;
  };

  return {
    client,
    async findClients(query) {
      const wanted = query.trim();
      if (wanted.length < 2) return [];
      try {
        // Search is by client ID, account number or mobile number, and finds at most one.
        const found = await http.request<{ clients: AccountJson[] }>(
          "GET",
          `/staff/clients/search?${new URLSearchParams({ q: wanted }).toString()}`,
        );
        const cards = await Promise.all(found.clients.map((account) => client(account.id)));
        return cards.filter((card): card is ClientCard => card !== null);
      } catch {
        return [];
      }
    },
    staffName(staffId) {
      let known = names.get(staffId);
      if (!known) {
        known = http
          .request<PeopleJson>("GET", `/staff/people?${new URLSearchParams({ ids: staffId }).toString()}`)
          .then(
            (json) => json.people.find((person) => person.id === staffId)?.name ?? null,
            () => {
              names.delete(staffId);
              return null;
            },
          );
        names.set(staffId, known);
      }
      return known;
    },
    async team() {
      try {
        const json = await http.request<PeopleJson>("GET", "/staff/people");
        for (const person of json.people) names.set(person.id, Promise.resolve(person.name));
        return json.people.map((person): StaffCard => ({ staffId: person.id, name: person.name }));
      } catch {
        return [];
      }
    },
  };
}
