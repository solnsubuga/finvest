import { ReturnsError, type PayoutReturn, type ReturnablePayout, type ReturnLine, type ReturnsApi } from "./api.ts";

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
  const ugx = (minor: number) => ({ minor, currency: "UGX" as const });
  const day = (minutes: number) => ago(minutes, now).slice(0, 10);
  const items: PayoutReturn[] =
    which === "sample"
      ? [
          {
            id: "-OdReturn00000000001",
            payoutId: "-OdBankPay00000000P1",
            clientId: "sample-client-joan",
            statementLineId: "-OdTrustLine00000031",
            reason: "Stanbic returned it: account closed. The line names our reference.",
            status: "proposed",
            proposedBy: OTHER,
            proposedAt: ago(35, now),
            payout: { kind: "bank", amount: ugx(1_200_000), bankName: "Stanbic Bank", network: null, maskedAccount: "•••• 7731", holderName: "Joan Namutebi" },
            line: {
              id: "-OdTrustLine00000031",
              valueDate: day(24 * 60),
              amount: ugx(1_200_000),
              reference: "RTN 0412 FINVEST -OdBankPay00000000P1",
              narrative: "Returned: account closed",
            },
          },
          {
            id: "-OdReturn00000000002",
            payoutId: "-OdMobPay000000000P2",
            clientId: "sample-client-peter",
            statementLineId: "-OdIotecLine00000044",
            reason: "MTN reversal on the ioTec statement for this payout.",
            status: "proposed",
            proposedBy: me,
            proposedAt: ago(10, now),
            payout: { kind: "mobile_money", amount: ugx(350_000), bankName: null, network: "mtn", maskedAccount: "•••• 5520", holderName: "Peter Okello" },
            line: { id: "-OdIotecLine00000044", valueDate: day(3 * 60), amount: ugx(350_000), reference: "REV 88412", narrative: "MTN reversal" },
          },
        ]
      : [];
  const paid: ReturnablePayout[] =
    which === "sample"
      ? [
          {
            id: "-OdBankPay00000000P3",
            clientId: "sample-client-amara",
            kind: "bank",
            amount: ugx(2_750_000),
            paidAt: ago(2 * 24 * 60, now),
            bankName: "Centenary Bank",
            network: null,
            maskedAccount: "•••• 4417",
            holderName: "Amara Nakato",
          },
          {
            id: "-OdMobPay000000000P4",
            clientId: "sample-client-david",
            kind: "mobile_money",
            amount: ugx(480_000),
            paidAt: ago(26 * 60, now),
            bankName: null,
            network: "airtel",
            maskedAccount: "•••• 3381",
            holderName: "David Mugisha",
          },
          {
            id: "-OdBankPay00000000P5",
            clientId: "sample-client-peter",
            kind: "bank",
            amount: ugx(2_750_000),
            paidAt: ago(6 * 24 * 60, now),
            bankName: "Stanbic Bank",
            network: null,
            maskedAccount: "•••• 0912",
            holderName: "Peter Okello",
          },
        ]
      : [];
  const lines: Record<string, ReturnLine[]> = {
    "-OdBankPay00000000P3": [
      { id: "-OdTrustLine00000052", valueDate: day(60), amount: ugx(2_750_000), reference: "RTN 0413 FINVEST -OdBankPay00000000P3", narrative: "Returned: account closed" },
      { id: "-OdTrustLine00000053", valueDate: day(30), amount: ugx(2_750_000), reference: "TRF 77120", narrative: "Incoming transfer" },
    ],
    "-OdMobPay000000000P4": [{ id: "-OdIotecLine00000061", valueDate: day(90), amount: ugx(480_000), reference: "REV 99231", narrative: "Airtel reversal" }],
  };
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
    listReturnable: (amount) =>
      wait(() =>
        paid.filter(
          (each) =>
            (!amount || (each.amount.minor === amount.minor && each.amount.currency === amount.currency)) &&
            !items.some((one) => one.payoutId === each.id && (one.status === "proposed" || one.status === "approved")),
        ),
      ),
    listReturnLines: (payoutId) =>
      wait(() =>
        (lines[payoutId] ?? []).filter((line) => !items.some((one) => one.statementLineId === line.id && (one.status === "proposed" || one.status === "approved"))),
      ),
    propose: (_key, payoutId, statementLineId, reason) =>
      wait(() => {
        if (items.some((each) => each.payoutId === payoutId && each.status !== "declined" && each.status !== "withdrawn")) refuse("return_already_recorded");
        if (!payoutId.startsWith("-Od")) refuse("not_found");
        if (items.some((each) => each.statementLineId === statementLineId && each.status === "proposed")) refuse("statement_line_in_use");
        made += 1;
        const chosen = paid.find((each) => each.id === payoutId);
        items.push({
          id: `-OdReturn000000000${String(made).padStart(2, "0")}`,
          payoutId,
          clientId: chosen?.clientId ?? "-OdClient00000000029",
          statementLineId,
          reason,
          status: "proposed",
          proposedBy: me,
          proposedAt: new Date().toISOString(),
          payout: chosen
            ? { kind: chosen.kind, amount: chosen.amount, bankName: chosen.bankName, network: chosen.network, maskedAccount: chosen.maskedAccount, holderName: chosen.holderName }
            : { kind: "bank", amount: ugx(1_000_000), bankName: null, network: null, maskedAccount: "•••• 0000", holderName: "A client" },
          line: (lines[payoutId] ?? []).find((each) => each.id === statementLineId) ?? null,
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
