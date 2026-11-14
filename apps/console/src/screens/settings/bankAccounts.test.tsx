import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { BankAccountsSection } from "./BankAccounts.tsx";
import { BankAccountsError, checkBankAccount, notConnectedBankAccountsApi, type BankAccountsApi, type NewBankAccount } from "./bankAccounts.ts";
import { createHttpBankAccountsApi } from "./bankAccountsHttp.ts";
import { createStubBankAccountsApi, type BankAccountsSample } from "./bankAccountsStub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const NOW = Date.parse("2026-11-14T09:00:00Z");

function renderSection({
  seed = "sample",
  api = createStubBankAccountsApi(ME, () => NOW, seed),
  canChange = true,
}: { seed?: BankAccountsSample; api?: BankAccountsApi; canChange?: boolean } = {}) {
  render(<BankAccountsSection api={api} directory={createStubDirectory(ME)} canChange={canChange} />);
  return { api, user: userEvent.setup() };
}

const shillings = () => screen.findByRole("article", { name: "Uganda shillings (UGX)" });
const dollars = () => screen.findByRole("article", { name: "US dollars (USD)" });
const waiting = (card: HTMLElement) => within(card).getByRole("region", { name: "Change waiting for a second person" });

describe("settings: bank account for transfers", () => {
  it("shows the account in use and the change someone else proposed, with what it changes", async () => {
    renderSection();
    const card = await shillings();
    expect(card).toHaveTextContent("Change waiting");
    expect(card).toHaveTextContent("9030001234567");
    expect(card).toHaveTextContent("Approved 3 Nov, 12:30");
    const change = waiting(card);
    expect(change).toHaveTextContent("9030007654321");
    expect(within(change).getAllByText("Changed")).toHaveLength(1);
    expect(change).toHaveTextContent("Proposed by Daniel Kato, 13 Nov, 10:10.");
    expect(within(change).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(await dollars()).toHaveTextContent("No account is set, so clients cannot pay in USD by bank transfer.");
  });

  it("approves someone else's change with a reason, and the new account is in use", async () => {
    const { user } = renderSection();
    const card = await shillings();
    await user.click(within(waiting(card)).getByRole("button", { name: "Approve" }));
    await user.click(within(card).getByRole("button", { name: "Approve the account" }));
    expect(within(card).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.type(within(card).getByLabelText("What did you check it against?"), "The bank's letter of 12 November");
    await user.click(within(card).getByRole("button", { name: "Approve the account" }));
    await waitFor(async () => expect(await shillings()).toHaveTextContent("In use"));
    const after = await shillings();
    expect(after).toHaveTextContent("9030007654321");
    expect(after).not.toHaveTextContent("9030001234567");
    expect(within(after).queryByRole("region", { name: "Change waiting for a second person" })).toBeNull();
  });

  it("lets the proposer only withdraw their own change", async () => {
    const { user } = renderSection({ seed: "mine" });
    const card = await shillings();
    const change = waiting(card);
    expect(change).toHaveTextContent("Proposed by you");
    expect(within(change).queryByRole("button", { name: "Approve" })).toBeNull();
    await user.click(within(change).getByRole("button", { name: "Withdraw it" }));
    await user.type(within(card).getByLabelText("Why withdraw it?"), "Wrong branch");
    await user.click(within(card).getByRole("button", { name: "Withdraw the change" }));
    await waitFor(async () => expect(await shillings()).toHaveTextContent("In use"));
    expect(await shillings()).toHaveTextContent("9030001234567");
  });

  it("sets a first account through a form that checks the details as they are typed", async () => {
    const { user } = renderSection({ seed: "empty" });
    const card = await shillings();
    expect(card).toHaveTextContent("Not set");
    await user.click(within(card).getByRole("button", { name: "Set the account" }));
    const form = within(card).getByRole("form", { name: "New UGX account details" });
    await user.type(within(form).getByLabelText(/Account number/), "12-34");
    expect(form).toHaveTextContent("Use 6 to 20 digits. Spaces and dashes are fine.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getAllByText("Write something here.")).toHaveLength(2);
    expect(form).toHaveTextContent("Say why, for the second person and the audit trail.");

    await user.type(within(form).getByLabelText(/^Bank/), "Example Bank Uganda");
    await user.type(within(form).getByLabelText(/Account name/), "Finvest Clients Trust Account");
    await user.clear(within(form).getByLabelText(/Account number/));
    await user.type(within(form).getByLabelText(/Account number/), "903 000 1234 567");
    await user.type(within(form).getByLabelText(/Why is it changing/), "Opening letter from the bank");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    await waitFor(async () => expect(await shillings()).toHaveTextContent("Change waiting"));
    const change = waiting(await shillings());
    expect(change).toHaveTextContent("9030001234567");
    expect(change).toHaveTextContent("Proposed by you");
    // Nothing is in use until someone else approves it.
    expect(await shillings()).toHaveTextContent("No account is set");
  });

  it("starts a change from the details in use once the waiting one is decided", async () => {
    const { user } = renderSection();
    const card = await shillings();
    await user.click(within(waiting(card)).getByRole("button", { name: "Reject" }));
    await user.type(within(card).getByLabelText("What is wrong with it?"), "Not from the bank");
    await user.click(within(card).getByRole("button", { name: "Reject the change" }));
    await user.click(await within(await shillings()).findByRole("button", { name: "Change the account" }));
    const form = within(await shillings()).getByRole("form", { name: "New UGX account details" });
    expect(within(form).getByLabelText(/^Bank/)).toHaveValue("Example Bank Uganda");
    expect(within(form).getByLabelText(/^Account number/)).toHaveValue("9030001234567");
    expect(within(await dollars()).getByRole("button", { name: "Set the account" })).toBeInTheDocument();
  });

  it("hides every change from a role that may only look", async () => {
    renderSection({ canChange: false });
    const card = await shillings();
    expect(within(card).queryByRole("button")).toBeNull();
    expect(card).toHaveTextContent("Someone who can change the bank account approves it.");
    expect(within(await dollars()).queryByRole("button")).toBeNull();
  });

  it("says when Finvest cannot be reached, and tries again", async () => {
    renderSection({ api: notConnectedBankAccountsApi });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The bank account could not be loaded");
    expect(alert).toHaveTextContent("not connected to the Finvest service yet");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows a refusal on the card and the account as Finvest now holds it", async () => {
    const stub = createStubBankAccountsApi(ME, () => NOW);
    const api: BankAccountsApi = { ...stub, decide: () => Promise.reject(new BankAccountsError("not_confirmed")) };
    const { user } = renderSection({ api });
    const card = await shillings();
    await user.click(within(waiting(card)).getByRole("button", { name: "Reject" }));
    await user.type(within(card).getByLabelText("What is wrong with it?"), "Not the trust account");
    await user.click(within(card).getByRole("button", { name: "Reject the change" }));
    expect(await within(await shillings()).findByRole("alert")).toHaveTextContent("Confirm it is you");
  });
});

describe("bank account rules", () => {
  const good: NewBankAccount = {
    bankName: "Example Bank",
    accountName: "Finvest",
    accountNumber: "903 000-1234",
    branch: "",
    reason: "Letter",
  };

  it("takes spaces and dashes in the number and needs a reason", () => {
    expect(checkBankAccount(good)).toEqual([]);
    expect(checkBankAccount({ ...good, reason: " " })).toEqual([{ field: "reason", code: "required" }]);
  });

  it("refuses what the backend refuses", () => {
    expect(checkBankAccount({ ...good, accountNumber: "12345" })).toEqual([{ field: "accountNumber", code: "invalid" }]);
    expect(checkBankAccount({ ...good, accountNumber: "1".repeat(21) })).toEqual([{ field: "accountNumber", code: "invalid" }]);
    expect(checkBankAccount({ ...good, bankName: "B".repeat(101) })).toEqual([{ field: "bankName", code: "too_long" }]);
    expect(checkBankAccount({ ...good, branch: "K".repeat(101) })).toEqual([{ field: "branch", code: "too_long" }]);
  });
});

describe("bank accounts over HTTP", () => {
  function fakeHttp(answer: (method: string, path: string, body: unknown) => unknown): { http: Http; calls: unknown[][] } {
    const calls: unknown[][] = [];
    const http = {
      request: vi.fn((method: string, path: string, body?: unknown) => {
        calls.push([method, path, body]);
        try {
          return Promise.resolve(answer(method, path, body));
        } catch (error) {
          return Promise.reject(error);
        }
      }),
    } as unknown as Http;
    return { http, calls };
  }

  it("reads each currency and sends the details as Finvest keeps them", async () => {
    const { http, calls } = fakeHttp((method) =>
      method === "GET"
        ? {
            accounts: [
              {
                currency: "UGX",
                in_use: { bank_name: "Bank", account_name: "Finvest", account_number: "1234567", branch: null },
                changed_at: "2026-11-03T09:30:00Z",
                proposal: null,
              },
            ],
          }
        : {},
    );
    const api = createHttpBankAccountsApi(http);
    expect(await api.list()).toEqual([
      {
        currency: "UGX",
        inUse: { bankName: "Bank", accountName: "Finvest", accountNumber: "1234567", branch: null },
        changedAt: "2026-11-03T09:30:00Z",
        proposal: null,
      },
    ]);
    await api.propose("UGX", { bankName: " Bank ", accountName: "Finvest", accountNumber: "123-4567", branch: " ", reason: " Letter " });
    expect(calls[1]).toEqual([
      "POST",
      "/staff/payments/bank-accounts/UGX/proposals",
      { account: { bank_name: "Bank", account_name: "Finvest", account_number: "1234567", branch: null }, reason: "Letter" },
    ]);
  });

  it("turns a refusal to decide into a second-person message, and field problems into marks", async () => {
    const forbidden = createHttpBankAccountsApi(
      fakeHttp(() => {
        throw new ApiError("forbidden", 403);
      }).http,
    );
    await expect(forbidden.decide("p1", "approved", "ok")).rejects.toMatchObject({ code: "same_person" });
    await expect(forbidden.list()).rejects.toMatchObject({ code: "forbidden" });
    const invalid = createHttpBankAccountsApi(
      fakeHttp(() => {
        throw new ApiError("validation_failed", 422, [{ field_path: ["account_number"], code: "invalid" }]);
      }).http,
    );
    await expect(invalid.propose("UGX", { bankName: "B", accountName: "F", accountNumber: "1", branch: "", reason: "r" })).rejects.toMatchObject({
      code: "invalid",
      problems: [{ field: "accountNumber", code: "invalid" }],
    });
  });
});
