/** The bank account clients pay into, over the real API (docs/payments.md). */

import { ApiError, type Http } from "@/api/http.ts";
import {
  BankAccountsError,
  tidiedAccount,
  type BankAccountDetails,
  type BankAccountField,
  type BankAccountProblem,
  type BankAccountsApi,
  type Currency,
  type CurrencyAccount,
} from "./bankAccounts.ts";

interface AccountJson {
  bank_name: string;
  account_name: string;
  account_number: string;
  branch: string | null;
}

interface ProposalJson {
  id: string;
  currency: Currency;
  account: AccountJson;
  reason: string;
  proposed_by: string;
  proposed_at: string;
  yours: boolean;
}

interface CurrencyJson {
  currency: Currency;
  in_use: AccountJson | null;
  changed_at: string | null;
  proposal: ProposalJson | null;
}

const account = (json: AccountJson): BankAccountDetails => ({
  bankName: json.bank_name,
  accountName: json.account_name,
  accountNumber: json.account_number,
  branch: json.branch,
});

export function toCurrencyAccount(json: CurrencyJson): CurrencyAccount {
  return {
    currency: json.currency,
    inUse: json.in_use ? account(json.in_use) : null,
    changedAt: json.changed_at,
    proposal: json.proposal
      ? {
          id: json.proposal.id,
          currency: json.proposal.currency,
          account: account(json.proposal.account),
          reason: json.proposal.reason,
          proposedBy: json.proposal.proposed_by,
          proposedAt: json.proposal.proposed_at,
          yours: json.proposal.yours,
        }
      : null,
  };
}

const FIELDS: Record<string, BankAccountField> = {
  bank_name: "bankName",
  account_name: "accountName",
  account_number: "accountNumber",
  branch: "branch",
  reason: "reason",
};

function problems(error: ApiError): BankAccountProblem[] {
  return error.fields.flatMap((problem) => {
    const field = FIELDS[String(problem.field_path[problem.field_path.length - 1])];
    if (!field) return [];
    // The backend's own codes, then Pydantic's for a field that is missing or too long.
    const code =
      problem.code === "required" || problem.code === "too_long" || problem.code === "invalid"
        ? problem.code
        : problem.code === "string_too_long"
          ? "too_long"
          : problem.code === "missing" || problem.code === "string_too_short"
            ? "required"
            : "not_accepted";
    return [{ field, code }];
  });
}

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toBankAccountsError(error: unknown, deciding = false): BankAccountsError {
  if (error instanceof BankAccountsError) return error;
  if (!(error instanceof ApiError)) return new BankAccountsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new BankAccountsError("invalid", problems(error));
    case "conflict":
      return new BankAccountsError("conflict");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new BankAccountsError("not_confirmed");
    case "forbidden":
      return new BankAccountsError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new BankAccountsError("not_found");
    default:
      return new BankAccountsError("unavailable");
  }
}

export function createHttpBankAccountsApi(http: Http): BankAccountsApi {
  return {
    async list() {
      try {
        const json = await http.request<{ accounts: CurrencyJson[] }>("GET", "/staff/payments/bank-accounts");
        return json.accounts.map(toCurrencyAccount);
      } catch (error) {
        throw toBankAccountsError(error);
      }
    },
    async propose(currency, input) {
      const tidied = tidiedAccount(input);
      try {
        await http.request("POST", `/staff/payments/bank-accounts/${encodeURIComponent(currency)}/proposals`, {
          account: {
            bank_name: tidied.bankName,
            account_name: tidied.accountName,
            account_number: tidied.accountNumber,
            branch: tidied.branch,
          },
          reason: input.reason.trim(),
        });
      } catch (error) {
        throw toBankAccountsError(error);
      }
    },
    async decide(proposalId, decision, reason) {
      try {
        await http.request("POST", `/staff/payments/bank-accounts/proposals/${encodeURIComponent(proposalId)}/decision`, {
          decision,
          reason: reason.trim(),
        });
      } catch (error) {
        throw toBankAccountsError(error, decision !== "withdrawn");
      }
    },
  };
}
