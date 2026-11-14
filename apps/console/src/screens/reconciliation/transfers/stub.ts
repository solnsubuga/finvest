import { PotTransfersError, type Pot, type PotTransfer, type PotTransfersApi } from "./api.ts";

/**
 * Sample pots and transfers for `pnpm dev` and previews. Not real money, and not connected to
 * Finvest. The rules here stand in for the backend's: the screen itself decides nothing.
 */

export type PotTransfersStubCase = "sample" | "empty" | "error";

const OTHER = "sample-colleague";

export const SAMPLE_POTS: Pot[] = [
  { source: "iotec", accountReference: "mtn", name: "ioTec wallet, MTN", currency: "UGX" },
  { source: "iotec", accountReference: "airtel", name: "ioTec wallet, Airtel", currency: "UGX" },
  { source: "bank", accountReference: "trust", name: "Trust bank account", currency: "UGX" },
];

const named = (source: Pot["source"], reference: string) => {
  const found = SAMPLE_POTS.find((each) => each.source === source && each.accountReference === reference);
  return { source, accountReference: reference, name: found?.name ?? reference };
};

function day(offset: number, now: Date): string {
  return new Date(now.getTime() + offset * 86_400_000).toISOString().slice(0, 10);
}

export function createStubPotTransfersApi(me: string, delay = 150, which: PotTransfersStubCase = "sample", now = new Date()): PotTransfersApi {
  const items: PotTransfer[] =
    which === "sample"
      ? [
          {
            id: "-OdPotTransfer000001",
            fromPot: named("iotec", "mtn"),
            toPot: named("bank", "trust"),
            sent: { minor: 48_000_000, currency: "UGX" },
            received: { minor: 47_985_000, currency: "UGX" },
            fee: { minor: 15_000, currency: "UGX" },
            valueDate: day(-1, now),
            providerReference: "IOT-SWEEP-20261113-01",
            status: "proposed",
            proposedBy: OTHER,
            proposedAt: new Date(now.getTime() - 50 * 60_000).toISOString(),
          },
          {
            id: "-OdPotTransfer000002",
            fromPot: named("iotec", "airtel"),
            toPot: named("bank", "trust"),
            sent: { minor: 9_500_000, currency: "UGX" },
            received: { minor: 9_500_000, currency: "UGX" },
            fee: { minor: 0, currency: "UGX" },
            valueDate: day(0, now),
            providerReference: "IOT-SWEEP-20261114-02",
            status: "proposed",
            proposedBy: me,
            proposedAt: new Date(now.getTime() - 12 * 60_000).toISOString(),
          },
        ]
      : [];
  let made = 2;
  const wait = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        if (which === "error") return reject(new PotTransfersError("unavailable"));
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new PotTransfersError("unavailable"));
        }
      }, delay),
    );
  const refuse = (code: ConstructorParameters<typeof PotTransfersError>[0]): never => {
    throw new PotTransfersError(code);
  };
  const open = (id: string) => {
    const found = items.find((each) => each.id === id) ?? refuse("not_found");
    if (found.status !== "proposed") refuse("transfer_already_decided");
    if (found.proposedBy === me) refuse("transfer_needs_second_person");
    return found;
  };
  return {
    listPots: () => wait(() => SAMPLE_POTS),
    listWaiting: () => wait(() => items.filter((each) => each.status === "proposed")),
    record: (_key, transfer) =>
      wait(() => {
        if (transfer.from.source === transfer.to.source && transfer.from.accountReference === transfer.to.accountReference) refuse("transfer_same_pot");
        if (transfer.received.minor > transfer.sent.minor) refuse("transfer_received_more_than_sent");
        if (transfer.from.source === "bank" && transfer.received.minor !== transfer.sent.minor) refuse("transfer_fee_not_allowed");
        if (transfer.valueDate > day(0, now)) refuse("transfer_in_the_future");
        if (items.some((each) => each.providerReference === transfer.providerReference && each.status !== "declined")) refuse("transfer_already_recorded");
        made += 1;
        items.push({
          id: `-OdPotTransfer0000${String(made).padStart(2, "0")}`,
          fromPot: named(transfer.from.source, transfer.from.accountReference),
          toPot: named(transfer.to.source, transfer.to.accountReference),
          sent: transfer.sent,
          received: transfer.received,
          fee: { minor: transfer.sent.minor - transfer.received.minor, currency: transfer.sent.currency },
          valueDate: transfer.valueDate,
          providerReference: transfer.providerReference,
          status: "proposed",
          proposedBy: me,
          proposedAt: new Date().toISOString(),
        });
      }),
    approve: (_key, id) =>
      wait(() => {
        open(id).status = "approved";
      }),
    decline: (_key, id) =>
      wait(() => {
        open(id).status = "declined";
      }),
  };
}
