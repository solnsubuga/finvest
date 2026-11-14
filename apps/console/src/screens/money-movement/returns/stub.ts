import { ReturnsError, type PayoutReturn, type ReturnsApi } from "./api.ts";

/**
 * Sample returns for `pnpm dev` and previews. Not real people or money, and not connected to
 * Finvest. The rules here stand in for the backend's: the screen itself decides nothing.
 */

export type ReturnsStubCase = "sample" | "empty" | "error";

const OTHER = "sample-colleague";

function ago(minutes: number, now: Date): string {
  return new Date(now.getTime() - minutes * 60_000).toISOString();
}

export function createStubReturnsApi(me: string, delay = 150, which: ReturnsStubCase = "sample", now = new Date()): ReturnsApi {
  const items: PayoutReturn[] =
    which === "sample"
      ? [
          {
            id: "-OdReturn00000000001",
            payoutId: "-OdBankPay00000000P1",
            clientId: "-OdClient00000000021",
            statementLineId: "-OdTrustLine00000031",
            reason: "Stanbic returned it: account closed. The line names our reference.",
            status: "proposed",
            proposedBy: OTHER,
            proposedAt: ago(35, now),
          },
          {
            id: "-OdReturn00000000002",
            payoutId: "-OdMobPay000000000P2",
            clientId: "-OdClient00000000022",
            statementLineId: "-OdIotecLine00000044",
            reason: "MTN reversal on the ioTec statement for this payout.",
            status: "proposed",
            proposedBy: me,
            proposedAt: ago(10, now),
          },
        ]
      : [];
  let made = 2;
  const wait = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        if (which === "error") return reject(new ReturnsError("unavailable"));
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new ReturnsError("unavailable"));
        }
      }, delay),
    );
  const refuse = (code: ConstructorParameters<typeof ReturnsError>[0]): never => {
    throw new ReturnsError(code);
  };
  const open = (id: string) => {
    const found = items.find((each) => each.id === id) ?? refuse("not_found");
    if (found.status !== "proposed") refuse("return_already_decided");
    return found;
  };
  return {
    listWaiting: () => wait(() => items.filter((each) => each.status === "proposed")),
    propose: (_key, payoutId, statementLineId, reason) =>
      wait(() => {
        if (items.some((each) => each.payoutId === payoutId && each.status !== "declined" && each.status !== "withdrawn")) refuse("return_already_recorded");
        if (!payoutId.startsWith("-Od")) refuse("not_found");
        if (items.some((each) => each.statementLineId === statementLineId && each.status === "proposed")) refuse("statement_line_in_use");
        made += 1;
        items.push({
          id: `-OdReturn000000000${String(made).padStart(2, "0")}`,
          payoutId,
          clientId: "-OdClient00000000029",
          statementLineId,
          reason,
          status: "proposed",
          proposedBy: me,
          proposedAt: new Date().toISOString(),
        });
      }),
    approve: (_key, id) =>
      wait(() => {
        const found = open(id);
        if (found.proposedBy === me) refuse("proposed_by_you");
        found.status = "approved";
      }),
    decline: (id) =>
      wait(() => {
        const found = open(id);
        if (found.proposedBy === me) refuse("proposed_by_you");
        found.status = "declined";
      }),
    withdraw: (id) =>
      wait(() => {
        const found = open(id);
        if (found.proposedBy !== me) refuse("not_proposed_by_you");
        found.status = "withdrawn";
      }),
  };
}
