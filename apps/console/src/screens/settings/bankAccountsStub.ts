import {
  BankAccountsError,
  checkBankAccount,
  tidiedAccount,
  type BankAccountDetails,
  type BankAccountProposal,
  type BankAccountsApi,
  type Currency,
  type CurrencyAccount,
} from "./bankAccounts.ts";

/**
 * A sample bank account for `pnpm dev` and previews. Not connected to Finvest, and the
 * details are made up. Changes stay in this browser tab. The stub refuses what the backend's
 * PostgreSQL rules refuse.
 *
 * `sample`: shillings in use, with a change proposed by someone else waiting.
 * `mine`: the same change, proposed by the signed-in person.
 * `empty`: no account set for any currency.
 */
export type BankAccountsSample = "sample" | "mine" | "empty";

const IN_USE: BankAccountDetails = {
  bankName: "Example Bank Uganda",
  accountName: "Finvest Clients Trust Account",
  accountNumber: "9030001234567",
  branch: "Kampala Road",
};

const PROPOSED: BankAccountDetails = {
  bankName: "Example Bank Uganda",
  accountName: "Finvest Clients Trust Account",
  accountNumber: "9030007654321",
  branch: "Kampala Road",
};

type Stored = { currency: Currency; inUse: BankAccountDetails | null; changedAt: string | null; proposal: Omit<BankAccountProposal, "yours"> | null };

export function createStubBankAccountsApi(me: { id: string }, now: () => number = Date.now, seed: BankAccountsSample = "sample"): BankAccountsApi {
  let counter = 0;
  const proposer = seed === "mine" ? me.id : "sample-staff-daniel";
  const rows: Stored[] = [
    {
      currency: "UGX",
      inUse: seed === "empty" ? null : IN_USE,
      changedAt: seed === "empty" ? null : "2026-11-03T09:30:00Z",
      proposal:
        seed === "empty"
          ? null
          : {
              id: "sample-bank-account-1",
              currency: "UGX",
              account: PROPOSED,
              reason: "The bank gave the trust account a new number. Letter from the bank dated 12 November.",
              proposedBy: proposer,
              proposedAt: "2026-11-13T07:10:00Z",
            },
    },
    { currency: "USD", inUse: null, changedAt: null, proposal: null },
  ];
  const view = (row: Stored): CurrencyAccount => ({
    ...row,
    proposal: row.proposal ? { ...row.proposal, yours: row.proposal.proposedBy === me.id } : null,
  });
  const settle = <T>(work: () => T): Promise<T> => {
    try {
      return Promise.resolve(work());
    } catch (error) {
      return Promise.reject(error);
    }
  };

  return {
    list: () => settle(() => rows.map(view)),
    propose: (currency, input) =>
      settle(() => {
        const problems = checkBankAccount(input);
        if (problems.length > 0) throw new BankAccountsError("invalid", problems);
        const row = rows.find((item) => item.currency === currency);
        if (!row) throw new BankAccountsError("not_found");
        if (row.proposal) throw new BankAccountsError("conflict");
        row.proposal = {
          id: `sample-bank-account-new-${++counter}`,
          currency,
          account: tidiedAccount(input),
          reason: input.reason.trim(),
          proposedBy: me.id,
          proposedAt: new Date(now()).toISOString(),
        };
      }),
    decide: (proposalId, decision, reason) =>
      settle(() => {
        const row = rows.find((item) => item.proposal?.id === proposalId);
        if (!row?.proposal) throw new BankAccountsError("not_found");
        // The proposer only withdraws, and never approves or rejects their own.
        if ((row.proposal.proposedBy === me.id) !== (decision === "withdrawn")) throw new BankAccountsError("same_person");
        if (reason.trim().length === 0) throw new BankAccountsError("invalid", [{ field: "reason", code: "required" }]);
        if (decision === "approved") {
          row.inUse = row.proposal.account;
          row.changedAt = new Date(now()).toISOString();
        }
        row.proposal = null;
      }),
  };
}
