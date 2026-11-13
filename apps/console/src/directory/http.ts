/**
 * The Directory over the real API.
 *
 * Staff names come from `GET /staff/people`, which every role may read. Clients come from
 * `GET /staff/clients/cards`, which needs `clients.view`; every role holds it. A lookup that
 * fails answers "unknown" rather than an error, as a name is never what a screen is for.
 */

import type { Http } from "@/api/http.ts";
import type { ClientCard, Directory, StaffCard } from "./api.ts";

interface PeopleJson {
  people: { id: string; name: string }[];
}

interface AccountJson {
  id: string;
}

interface CardJson {
  id: string;
  verified_name: string | null;
  account_number: string | null;
  created_at: string;
}

/** A Kampala date, `YYYY-MM-DD`. */
function kampalaDay(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date(iso));
}

export function toClientCard(json: CardJson): ClientCard {
  return {
    clientId: json.id,
    name: json.verified_name ?? "Name not yet verified",
    accountNumber: json.account_number,
    // The name and the account number come only with an approved identity check.
    verified: json.verified_name !== null,
    clientSince: kampalaDay(json.created_at),
  };
}

/** The most IDs `GET /staff/clients/cards` takes at once. */
const MAX_CARDS = 100;

export function createHttpDirectory(http: Http): Directory {
  const names = new Map<string, Promise<string | null>>();
  const clients = new Map<string, Promise<ClientCard | null>>();

  // IDs asked for in the same moment, such as every row of an inbox, go in one request.
  let waiting = new Map<string, (card: ClientCard | null) => void>();

  async function fetchCards(batch: Map<string, (card: ClientCard | null) => void>) {
    const ids = [...batch.keys()];
    for (let start = 0; start < ids.length; start += MAX_CARDS) {
      const chunk = ids.slice(start, start + MAX_CARDS);
      const query = new URLSearchParams(chunk.map((id) => ["ids", id])).toString();
      const found = await http.request<{ cards: CardJson[] }>("GET", `/staff/clients/cards?${query}`).then(
        (json) => new Map(json.cards.map((card) => [card.id, toClientCard(card)])),
        () => null,
      );
      for (const id of chunk) {
        // A failed read is asked again next time; a client who does not exist is remembered.
        if (found === null) clients.delete(id);
        batch.get(id)?.(found?.get(id) ?? null);
      }
    }
  }

  const client = (clientId: string): Promise<ClientCard | null> => {
    let known = clients.get(clientId);
    if (!known) {
      known = new Promise((resolve) => {
        if (waiting.size === 0) {
          queueMicrotask(() => {
            const batch = waiting;
            waiting = new Map();
            void fetchCards(batch);
          });
        }
        waiting.set(clientId, resolve);
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
