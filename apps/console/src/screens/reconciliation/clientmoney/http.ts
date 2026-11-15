import { ApiError, type Http } from "@/api/http.ts";
import type { Amount } from "../api.ts";
import { ClientMoneyError, type CheckLine, type ClientMoneyApi, type ClientMoneyCheck, type HeldPot, type Standing } from "./api.ts";

interface LineJson {
  code: string;
  name: string;
  amount: Amount;
}

interface CheckJson {
  business_date: string;
  standing: Standing;
  owed: Amount;
  interest_owed: Amount;
  pots: {
    source: HeldPot["source"];
    account_reference: string;
    name: string;
    statement_closing: Amount;
    ledger_closing: Amount;
    open_differences: Amount;
  }[];
  on_statements: Amount;
  bills: Amount;
  custodian: { check_id: string; statement_date: string; differences: number } | null;
  discount_to_come: Amount;
  not_client_money: LineJson[];
  not_client_money_total: Amount;
  held: Amount;
  difference: Amount;
  open_differences: Amount;
  not_on_statement: LineJson[];
  not_on_statement_total: Amount;
  unexplained: Amount;
}

const line = (json: LineJson): CheckLine => ({ code: json.code, name: json.name, amount: json.amount });

export function fromCheck(json: CheckJson): ClientMoneyCheck {
  return {
    businessDate: json.business_date,
    standing: json.standing,
    owed: json.owed,
    interestOwed: json.interest_owed,
    pots: json.pots.map((pot) => ({
      source: pot.source,
      accountReference: pot.account_reference,
      name: pot.name,
      statementClosing: pot.statement_closing,
      ledgerClosing: pot.ledger_closing,
      openDifferences: pot.open_differences,
    })),
    onStatements: json.on_statements,
    bills: json.bills,
    custodian: json.custodian && {
      checkId: json.custodian.check_id,
      statementDate: json.custodian.statement_date,
      differences: json.custodian.differences,
    },
    discountToCome: json.discount_to_come,
    notClientMoney: json.not_client_money.map(line),
    notClientMoneyTotal: json.not_client_money_total,
    held: json.held,
    difference: json.difference,
    openDifferences: json.open_differences,
    notOnStatement: json.not_on_statement.map(line),
    notOnStatementTotal: json.not_on_statement_total,
    unexplained: json.unexplained,
  };
}

export function createHttpClientMoneyApi(http: Http): ClientMoneyApi {
  return {
    async get() {
      try {
        const found = await http.request<{ check: CheckJson | null }>("GET", "/staff/reconciliation/client-money");
        return found.check ? fromCheck(found.check) : null;
      } catch (error) {
        throw new ClientMoneyError(error instanceof ApiError && error.code === "forbidden" ? "forbidden" : "unavailable");
      }
    },
  };
}
