import type { ClientCard, Directory } from "./api.ts";

/** Sample clients and staff for `pnpm dev` and previews. Not real people. */
export const SAMPLE_CLIENTS: readonly ClientCard[] = [
  { clientId: "sample-client-amara", name: "Amara Nakato", accountNumber: "1001004827", verified: true, clientSince: "2026-10-10", status: "active" },
  { clientId: "sample-client-peter", name: "Peter Okello", accountNumber: "1001005130", verified: true, clientSince: "2026-09-02", status: "active" },
  { clientId: "sample-client-david", name: "David Mugisha", accountNumber: null, verified: false, clientSince: "2026-11-12", status: "active" },
  { clientId: "sample-client-joan", name: "Joan Namutebi", accountNumber: "1001003318", verified: true, clientSince: "2026-06-21", status: "active" },
  { clientId: "sample-client-sarah", name: "Sarah Achieng", accountNumber: "1001006044", verified: true, clientSince: "2026-08-15", status: "active" },
  { clientId: "sample-client-ivan", name: "Ivan Ssemanda", accountNumber: "1001002291", verified: true, clientSince: "2026-03-30", status: "locked" },
  { clientId: "sample-client-kira", name: "Kira Road Savings Group", accountNumber: "1001007405", verified: true, clientSince: "2026-07-01", status: "active" },
];

const SAMPLE_STAFF: Record<string, string> = {
  "sample-staff-grace": "Grace Auma",
  "sample-staff-daniel": "Daniel Kato",
};

export function createStubDirectory(me?: { id: string; name: string }): Directory {
  const staff = { ...SAMPLE_STAFF, ...(me ? { [me.id]: me.name } : {}) };
  return {
    client: (clientId) => Promise.resolve(SAMPLE_CLIENTS.find((client) => client.clientId === clientId) ?? null),
    findClients: (query) => {
      const wanted = query.trim().toLowerCase();
      if (wanted.length < 2) return Promise.resolve([]);
      return Promise.resolve(
        SAMPLE_CLIENTS.filter(
          (client) => client.name.toLowerCase().includes(wanted) || (client.accountNumber ?? "").startsWith(wanted),
        ),
      );
    },
    staffName: (staffId) => Promise.resolve(staff[staffId] ?? null),
    team: () => Promise.resolve(Object.entries(staff).map(([staffId, name]) => ({ staffId, name }))),
  };
}
