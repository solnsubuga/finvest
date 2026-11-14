import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PayoutsError, notConnectedPayoutsApi, type PayoutsApi } from "./api.ts";
import { MoneyMovementScreen } from "./index.tsx";
import { createStubPayoutsApi } from "./stub.ts";

const ME = "-Nstaff000000000000A";

function renderScreen(api: PayoutsApi = createStubPayoutsApi(ME, 0), canSettle = true) {
  let keys = 0;
  render(<MoneyMovementScreen api={api} me={ME} canSettle={canSettle} newKey={() => `key-${++keys}`} />);
  return userEvent.setup();
}

const stranded = () => screen.getByRole("list", { name: "Payouts never answered for" });

describe("money movement screen", () => {
  it("lists payouts held up at ioTec, longest first", async () => {
    renderScreen();
    const table = await screen.findByRole("table", { name: "Payouts held up at ioTec, longest first" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual(["UGX 2,400,000", "UGX 350,000"]);
    expect(rows[0]).toHaveTextContent("11");
  });

  it("proposes how a stranded payout ended, with the evidence", async () => {
    const api = createStubPayoutsApi(ME, 0);
    const propose = vi.spyOn(api, "propose");
    const user = renderScreen(api);
    await screen.findByRole("list", { name: "Payouts never answered for" });
    const form = screen.getByRole("form", { name: "Propose how it ended" });
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose what the statement shows.");

    await user.click(within(form).getByRole("radio", { name: /It never left/ }));
    await user.type(within(form).getByLabelText("Statement ID"), "-NstatementA00000001");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Give a reason.");
    await user.type(within(form).getByLabelText("What you checked"), "No line on any day since the send");
    await user.click(within(form).getByRole("button", { name: "Propose" }));

    expect(propose).toHaveBeenCalledWith("key-1", "-OdPay0000000000003C", {
      outcome: "failed",
      statementId: "-NstatementA00000001",
      reason: "No line on any day since the send",
    });
    expect(await screen.findByText("Proposed. A different person must now approve it.")).toBeInTheDocument();
    expect(within(stranded()).getAllByRole("button")[0]).toHaveTextContent("Waiting for a second person");
  });

  it("lets a second person approve, and the payout leaves the list", async () => {
    const user = renderScreen();
    await screen.findByRole("list", { name: "Payouts never answered for" });
    await user.click(within(stranded()).getByRole("button", { name: /UGX 5,000,000/ }));
    expect(screen.getByText(/A colleague proposed/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Your reason"), "Checked every statement");
    await user.click(screen.getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByText("Settled.")).toBeInTheDocument();
    expect(within(stranded()).queryByRole("button", { name: /UGX 5,000,000/ })).not.toBeInTheDocument();
  });

  it("offers the proposer only to withdraw", async () => {
    const user = renderScreen();
    await screen.findByRole("list", { name: "Payouts never answered for" });
    await user.click(within(stranded()).getByRole("button", { name: /UGX 780,000/ }));
    expect(screen.getByText(/You proposed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Why you are withdrawing it"), "Wrong line");
    await user.click(screen.getByRole("button", { name: "Withdraw my proposal" }));
    expect(await screen.findByText("Withdrawn. The payout is waiting for a new proposal.")).toBeInTheDocument();
  });

  it("keeps the same idempotency key when an approval's answer never arrived", async () => {
    const api = createStubPayoutsApi(ME, 0);
    const approve = vi.spyOn(api, "approve").mockRejectedValueOnce(new PayoutsError("unavailable"));
    const user = renderScreen(api);
    await screen.findByRole("list", { name: "Payouts never answered for" });
    await user.click(within(stranded()).getByRole("button", { name: /UGX 5,000,000/ }));
    await user.type(screen.getByLabelText("Your reason"), "Checked");
    await user.click(screen.getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest.");
    await user.click(screen.getByRole("button", { name: "Approve as second person" }));
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-1"]);
  });

  it("treats an unnamed conflict as a definite refusal: a new key, and the list reloads", async () => {
    const api = createStubPayoutsApi(ME, 0);
    const approve = vi.spyOn(api, "approve").mockRejectedValueOnce(new PayoutsError("refused"));
    const reload = vi.spyOn(api, "listStranded");
    const user = renderScreen(api);
    await screen.findByRole("list", { name: "Payouts never answered for" });
    await user.click(within(stranded()).getByRole("button", { name: /UGX 5,000,000/ }));
    await user.type(screen.getByLabelText("Your reason"), "Checked");
    await user.click(screen.getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Finvest refused this as things now stand.");
    expect(reload).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Approve as second person" }));
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-2"]);
  });

  it("says when the person did not confirm it is them", async () => {
    const api = createStubPayoutsApi(ME, 0);
    vi.spyOn(api, "approve").mockRejectedValueOnce(new PayoutsError("not_confirmed"));
    const user = renderScreen(api);
    await screen.findByRole("list", { name: "Payouts never answered for" });
    await user.click(within(stranded()).getByRole("button", { name: /UGX 5,000,000/ }));
    await user.type(screen.getByLabelText("Your reason"), "Checked");
    await user.click(screen.getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was done, because you did not confirm it is you.");
  });

  it("shows empty lists plainly", async () => {
    renderScreen(createStubPayoutsApi(ME, 0, "empty"));
    expect(await screen.findByText(/Nothing is held up/)).toBeInTheDocument();
    expect(await screen.findByText("Every payout ioTec was asked about has an answer.")).toBeInTheDocument();
  });

  it("shows a failure with a way to try again", async () => {
    renderScreen(createStubPayoutsApi(ME, 0, "error"));
    expect(await screen.findByText("Payouts held up at ioTec could not be loaded")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Try again" })).toHaveLength(2);
  });

  it("hides settling from staff without reconciliation", async () => {
    renderScreen(createStubPayoutsApi(ME, 0), false);
    await screen.findByRole("table");
    expect(screen.queryByText("Never answered for")).not.toBeInTheDocument();
  });

  it("says when the console is not connected", async () => {
    renderScreen(notConnectedPayoutsApi);
    expect(await screen.findAllByText("The console is not connected to Finvest yet.")).toHaveLength(2);
  });
});
