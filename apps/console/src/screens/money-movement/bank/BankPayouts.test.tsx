import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { BankPayoutsError, notConnectedBankPayoutsApi, type BankPayoutsApi } from "./api.ts";
import { BankPayouts } from "./BankPayouts.tsx";
import { createStubBankPayoutsApi } from "./stub.ts";

const ME = "-Nstaff000000000000A";

function renderSection(api: BankPayoutsApi = createStubBankPayoutsApi(ME, 0), canChange = true, save = vi.fn()) {
  let keys = 0;
  render(<BankPayouts api={api} me={ME} canChange={canChange} newKey={() => `key-${++keys}`} save={save} directory={createStubDirectory()} />);
  return { user: userEvent.setup(), save };
}

const batches = () => screen.getByRole("list", { name: "Batches" });

describe("bank payouts", () => {
  it("prepares a batch from the payouts chosen, with a reason", async () => {
    const api = createStubBankPayoutsApi(ME, 0);
    const prepare = vi.spyOn(api, "prepare");
    const { user } = renderSection(api);
    const table = await screen.findByRole("table", { name: "Bank payouts waiting to be sent, oldest first" });
    expect(within(table).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual([
      "UGX 4,500,000",
      "UGX 1,250,000",
      "UGX 800,000",
      "UGX 12,000,000",
    ]);
    const form = screen.getByRole("form", { name: "Prepare a batch" });
    await user.click(within(form).getByRole("button", { name: "Prepare batch" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose the payouts");

    await user.click(screen.getByRole("checkbox", { name: /UGX 4,500,000/ }));
    await user.click(screen.getByRole("checkbox", { name: /UGX 800,000/ }));
    expect(form).toHaveTextContent("2 chosen, UGX 5,300,000");
    await user.click(within(form).getByRole("button", { name: "Prepare batch" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Give a reason");
    await user.type(within(form).getByLabelText("Reason for this batch"), "Wednesday bank payouts");
    await user.click(within(form).getByRole("button", { name: "Prepare batch" }));

    expect(prepare).toHaveBeenCalledWith("key-1", "UGX", ["-OdBankPay0000000001", "-OdBankPay0000000003"], "Wednesday bank payouts");
    expect(await within(form).findByText(/Batch prepared/)).toBeInTheDocument();
    expect(within(table).getAllByRole("rowheader")).toHaveLength(2);
    expect(within(batches()).getAllByRole("article")).toHaveLength(5);
  });

  it("lists each payout of a batch for the second person, with its account masked", async () => {
    renderSection();
    const batch = await screen.findByRole("article", { name: "Batch of UGX 5,000,000" });
    const table = await within(batch).findByRole("table", { name: "Payouts in this batch" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual(["UGX 3,000,000", "UGX 2,000,000"]);
    expect(rows[0]).toHaveTextContent(/••••\s\d{4}/);
    // The holder name is on the payout; the client name arrives when the directory answers.
    await waitFor(() => expect(within(rows[0]!).getAllByText("Kira Road Savings Group")).toHaveLength(2));
    // An approved batch's payouts are in its file, not on the card.
    const approved = screen.getByRole("article", { name: "Batch of UGX 7,350,000" });
    expect(within(approved).queryByRole("table")).not.toBeInTheDocument();
  });

  it("names the clients waiting through the directory", async () => {
    renderSection();
    const table = await screen.findByRole("table", { name: /waiting to be sent/ });
    expect(await within(table).findByText("Peter Okello")).toBeInTheDocument();
  });

  it("shows each currency's payouts on its own", async () => {
    const { user } = renderSection();
    await screen.findByRole("table", { name: /waiting to be sent/ });
    await user.click(screen.getByRole("button", { name: "USD" }));
    const table = await screen.findByRole("table", { name: /waiting to be sent/ });
    expect(within(table).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["$1,500.00"]);
  });

  it("lets a second person approve a batch, and its payouts count as sent", async () => {
    const api = createStubBankPayoutsApi(ME, 0);
    const approve = vi.spyOn(api, "approve");
    const { user } = renderSection(api);
    const batch = await screen.findByRole("article", { name: "Batch of UGX 5,000,000" });
    expect(batch).toHaveTextContent("Needs a second person");
    await user.type(within(batch).getByLabelText("Your reason"), "Checked each account");
    await user.click(within(batch).getByRole("button", { name: "Approve as second person" }));
    expect(approve).toHaveBeenCalledWith("key-1", "-OdBankBatch00000001", "Checked each account");
    expect(await screen.findByText(/Approved. The payouts count as sent/)).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Bank payouts sent" })).getAllByRole("button")).toHaveLength(10);
  });

  it("offers the person who prepared a batch only to withdraw it", async () => {
    const { user } = renderSection();
    await screen.findByRole("table", { name: /waiting to be sent/ });
    await user.click(screen.getByRole("checkbox", { name: "Choose all" }));
    await user.type(screen.getByLabelText("Reason for this batch"), "Everything waiting");
    await user.click(screen.getByRole("button", { name: "Prepare batch" }));
    const mine = await screen.findByRole("article", { name: "Batch of UGX 18,550,000" });
    expect(mine).toHaveTextContent("Waiting for a second person");
    expect(within(mine).queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
    await user.type(within(mine).getByLabelText("Why you are withdrawing it"), "Wrong day");
    await user.click(within(mine).getByRole("button", { name: "Withdraw" }));
    expect(await screen.findByText(/Withdrawn. Its payouts are waiting/)).toBeInTheDocument();
  });

  it("downloads the transfer file of an approved batch", async () => {
    const { user, save } = renderSection();
    const approved = await screen.findByRole("article", { name: "Batch of UGX 7,350,000" });
    await user.click(within(approved).getByRole("button", { name: "Download transfer file" }));
    expect(save).toHaveBeenCalledWith(expect.any(Blob), "finvest-bank-payouts--OdBankBatch00000002.csv");
  });

  it("proposes a settlement from the trust statement, and a second person approves another", async () => {
    const api = createStubBankPayoutsApi(ME, 0);
    const propose = vi.spyOn(api, "propose");
    const { user } = renderSection(api);
    const sent = await screen.findByRole("list", { name: "Bank payouts sent" });
    await user.click(within(sent).getByRole("button", { name: /UGX 950,000/ }));
    const form = screen.getByRole("form", { name: "Propose how it ended" });
    await user.click(within(form).getByRole("radio", { name: /It was paid/ }));
    await user.type(within(form).getByLabelText("Statement line ID"), "-NtrustLine000000001");
    await user.type(within(form).getByLabelText("What you checked"), "Line on the trust statement");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(propose).toHaveBeenCalledWith("key-1", "-OdBankPay0000000009", {
      outcome: "paid",
      statementLineId: "-NtrustLine000000001",
      reason: "Line on the trust statement",
    });
    expect(await screen.findByText("Proposed. A different person must now approve it.")).toBeInTheDocument();

    await user.click(within(sent).getByRole("button", { name: /UGX 6,400,000/ }));
    const card = screen.getByRole("article", { name: "Bank payout of UGX 6,400,000" });
    await user.type(within(card).getByLabelText("Your reason"), "Opened the line");
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByText("Settled.")).toBeInTheDocument();
    expect(within(sent).queryByRole("button", { name: /UGX 6,400,000/ })).not.toBeInTheDocument();
  });

  it("words a refusal, and keeps the same key for a lost answer", async () => {
    const api = createStubBankPayoutsApi(ME, 0);
    const approve = vi
      .spyOn(api, "approve")
      .mockRejectedValueOnce(new BankPayoutsError("unavailable"))
      .mockRejectedValueOnce(new BankPayoutsError("statement_line_in_use"));
    const { user } = renderSection(api);
    const batch = await screen.findByRole("article", { name: "Batch of UGX 5,000,000" });
    await user.type(within(batch).getByLabelText("Your reason"), "Checked");
    await user.click(within(batch).getByRole("button", { name: "Approve as second person" }));
    expect(await within(batch).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(within(batch).getByRole("button", { name: "Approve as second person" }));
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-1"]);
  });

  it("shows only the lists to someone who cannot pay bank payouts", async () => {
    renderSection(undefined, false);
    await screen.findByRole("table", { name: /waiting to be sent/ });
    expect(screen.queryByRole("form", { name: "Prepare a batch" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download transfer file" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
  });

  it("shows empty and failed lists", async () => {
    renderSection(createStubBankPayoutsApi(ME, 0, "empty"));
    expect(await screen.findByText(/No UGX bank payouts are waiting/)).toBeInTheDocument();
    expect(screen.getByText(/No batch is waiting/)).toBeInTheDocument();
    expect(screen.getByText(/Every bank payout sent has been settled/)).toBeInTheDocument();
  });

  it("says when the console is not connected", async () => {
    renderSection(notConnectedBankPayoutsApi);
    expect(await screen.findByRole("heading", { name: "Bank payouts waiting to be sent could not be loaded" })).toBeInTheDocument();
    // Waiting, batches, sent and bulk debits each say so.
    expect(screen.getAllByText("The console is not connected to Finvest yet.")).toHaveLength(4);
  });

  it("settles a batch from one bulk debit once the payouts add up to the line", async () => {
    const api = createStubBankPayoutsApi(ME, 0);
    const proposeBulk = vi.spyOn(api, "proposeBulk");
    const { user } = renderSection(api);
    const form = await screen.findByRole("form", { name: "Settle a batch from one debit" });
    // Each batch is told apart by when it was approved and how many payouts are left.
    const choice = within(form).getByRole("button", { name: /-OdBankBatch00000004/ });
    expect(choice).toHaveTextContent(/^Approved .+, 3 payouts left-OdBankBatch00000004$/);
    await user.click(choice);
    const line = await within(form).findByRole("radio", { name: /UGX 10,000,000/ });
    // Every payout of the batch starts chosen.
    expect(within(form).getByRole("status")).toHaveTextContent("3 chosen, UGX 10,000,000. Choose the line.");
    await user.click(line);
    expect(within(form).getByRole("status")).toHaveTextContent("Adds up to the line exactly.");
    await user.click(within(form).getByRole("checkbox", { name: /UGX 1,750,000/ }));
    expect(within(form).getByRole("status")).toHaveTextContent("The line is UGX 1,750,000 more than the payouts chosen.");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("must add up to the line exactly");
    await user.click(within(form).getByRole("checkbox", { name: /UGX 1,750,000/ }));
    await user.type(within(form).getByLabelText("What you checked"), "One debit, BULK 0412");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(proposeBulk).toHaveBeenCalledWith(
      "key-1",
      "-OdBankBatch00000004",
      "-OdTrustLine00000031",
      ["-OdBankPay0000000011", "-OdBankPay0000000012", "-OdBankPay0000000013"],
      "One debit, BULK 0412",
    );
    expect(await screen.findByText(/A different person must now approve the bulk debit/)).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Bulk debit of UGX 10,000,000" })).toHaveTextContent("Waiting for a second person");
  });

  it("says Loading for a bulk debit's payouts until the sent list is in", async () => {
    const api = createStubBankPayoutsApi(ME, 0);
    vi.spyOn(api, "listSent").mockReturnValue(new Promise(() => undefined));
    renderSection(api);
    const bulk = await screen.findByRole("article", { name: "Bulk debit of UGX 3,000,000" });
    expect(within(bulk).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["Loading", "Loading"]);
  });

  it("lets a second person approve a colleague's bulk debit, and its payouts are settled", async () => {
    const api = createStubBankPayoutsApi(ME, 0);
    const approveBulk = vi.spyOn(api, "approveBulk");
    const { user } = renderSection(api);
    const bulk = await screen.findByRole("article", { name: "Bulk debit of UGX 3,000,000" });
    expect(within(bulk).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["UGX 2,400,000", "UGX 600,000"]);
    // The second person sees the debit line itself, not only its ID.
    const debit = within(bulk).getByRole("region", { name: "The debit on the trust statement" });
    expect(debit).toHaveTextContent("UGX 3,000,000");
    expect(debit).toHaveTextContent("BULK 0409 FINVEST");
    expect(debit).toHaveTextContent("Bulk transfer, 2 items");
    // Its payouts point to the bulk instead of offering their own decision.
    const sent = screen.getByRole("list", { name: "Bank payouts sent" });
    await user.click(within(sent).getByRole("button", { name: /UGX 2,400,000/ }));
    const card = screen.getByRole("article", { name: "Bank payout of UGX 2,400,000" });
    expect(card).toHaveTextContent("Decide it with the rest of the bulk debit");
    expect(within(card).queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();

    await user.type(within(bulk).getByLabelText("Your reason"), "Opened the statement");
    await user.click(within(bulk).getByRole("button", { name: "Approve as second person" }));
    expect(approveBulk).toHaveBeenCalledWith("key-1", "-OdBankBulk000000001", "Opened the statement");
    expect(await screen.findByText(/Every payout in the bulk debit is settled as paid/)).toBeInTheDocument();
    expect(within(sent).queryByRole("button", { name: /UGX 2,400,000/ })).not.toBeInTheDocument();
  });
});
