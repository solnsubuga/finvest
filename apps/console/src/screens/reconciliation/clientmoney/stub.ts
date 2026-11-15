import type { Amount } from "../api.ts";
import { ClientMoneyError, type ClientMoneyApi, type ClientMoneyCheck } from "./api.ts";

/**
 * A sample client money check for `pnpm dev` and previews. Not real money, and not connected to
 * Finvest. `short` is a day with balances carried over from the paper register that are not yet
 * in a pot, and a payout the bank had not shown; `empty` is before any day is closed.
 */

export type ClientMoneyStubCase = "sample" | "short" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });

function sample(which: "sample" | "short"): ClientMoneyCheck {
  const short = which === "short";
  const trust = short ? 411_500_000 : 412_000_000;
  const carried = short ? 18_000_000 : 0;
  const onStatements = 48_250_000 + 12_600_000 + 3_150_000 + trust;
  const bills = 4_380_000_000;
  const notClient = 6_400_000 + 250_000_000 - 35_600_000;
  const held = onStatements + bills - notClient;
  const owed = 4_635_200_000 + carried;
  const pot = (source: "iotec" | "pesapal" | "bank", accountReference: string, name: string, statement: number, ledger = statement) => ({
    source,
    accountReference,
    name,
    statementClosing: ugx(statement),
    ledgerClosing: ugx(ledger),
    openDifferences: ugx(statement - ledger),
  });
  return {
    businessDate: "2026-11-14",
    standing: held === owed ? "ties" : held < owed ? "short" : "over",
    owed: ugx(owed),
    interestOwed: ugx(41_800_000),
    pots: [
      pot("iotec", "mtn", "ioTec wallet", 48_250_000),
      pot("iotec", "airtel", "ioTec wallet", 12_600_000),
      pot("pesapal", "cards", "Pesapal balance", 3_150_000),
      pot("bank", "trust", "Trust bank account", trust, 412_000_000),
    ],
    onStatements: ugx(onStatements),
    bills: ugx(bills),
    custodian: short
      ? { checkId: "-OdCustodian000002", statementDate: "2026-11-07", differences: 1 }
      : { checkId: "-OdCustodian000003", statementDate: "2026-11-14", differences: 0 },
    discountToCome: ugx(120_000_000),
    notClientMoney: [
      { code: "dealing.buffer_capital", name: "Finvest's own money in the buffer", amount: ugx(250_000_000) },
      { code: "tax.withholding_payable", name: "Tax withheld from interest", amount: ugx(6_400_000) },
      { code: "earnings", name: "Finvest's earnings so far, income less costs", amount: ugx(-35_600_000) },
    ],
    notClientMoneyTotal: ugx(notClient),
    held: ugx(held),
    difference: ugx(held - owed),
    openDifferences: ugx(trust - 412_000_000),
    notOnStatement: short ? [{ code: "migration.opening_balances", name: "Balances carried over from the paper register", amount: ugx(carried) }] : [],
    notOnStatementTotal: ugx(carried),
    unexplained: ugx(0),
  };
}

export function createStubClientMoneyApi(delay = 150, which: ClientMoneyStubCase = "sample"): ClientMoneyApi {
  const wait = () => new Promise((resolve) => setTimeout(resolve, delay));
  return {
    async get() {
      await wait();
      if (which === "error") throw new ClientMoneyError("unavailable");
      if (which === "empty") return null;
      return sample(which);
    },
  };
}
