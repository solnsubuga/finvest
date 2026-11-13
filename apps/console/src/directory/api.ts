/**
 * Who a client or member of staff is, for screens that only hold their ID.
 *
 * The support and complaints routes return `client_id` and staff IDs, never
 * names: those modules do not read identity tables. The console looks the
 * names up here. Until the clients and team routes exist, the only
 * implementation is the sample in `stub.ts`.
 */

export interface ClientCard {
  clientId: string;
  name: string;
  /** The 10 digit Finvest account number, once the identity check has passed. */
  accountNumber: string | null;
  verified: boolean;
  /** `YYYY-MM-DD`. */
  clientSince: string;
}

export interface StaffCard {
  staffId: string;
  name: string;
}

export interface Directory {
  client(clientId: string): Promise<ClientCard | null>;
  /** Clients whose name, phone number or account number matches, for logging a complaint. */
  findClients(query: string): Promise<ClientCard[]>;
  staffName(staffId: string): Promise<string | null>;
  /** Staff who can be given a complaint to work. */
  team(): Promise<StaffCard[]>;
}

/** Used in builds with no backend client and the stub turned off: nobody has a name yet. */
export const emptyDirectory: Directory = {
  client: () => Promise.resolve(null),
  findClients: () => Promise.resolve([]),
  staffName: () => Promise.resolve(null),
  team: () => Promise.resolve([]),
};
