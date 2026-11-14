import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReturnsError, type ReturnsApi } from "./api.ts";
import { Returns } from "./Returns.tsx";
import { createStubReturnsApi } from "./stub.ts";

const ME = "-Nstaff000000000000A";

function renderSection(api: ReturnsApi = createStubReturnsApi(ME, 0), canChange = true) {
  let keys = 0;
  render(<Returns api={api} me={ME} canChange={canChange} newKey={() => `key-${++keys}`} />);
  return userEvent.setup();
}

describe("payouts that came back", () => {
  it("finds the paid payout by amount, then picks its line, and proposes the return", async () => {
    const api = createStubReturnsApi(ME, 0);
    const propose = vi.spyOn(api, "propose");
    const listReturnable = vi.spyOn(api, "listReturnable");
    const user = renderSection(api);
    const form = screen.getByRole("form", { name: "Record a payout that came back" });
    const payouts = await within(form).findByRole("group", { name: "The payout that came back" });
    expect(within(payouts).getAllByRole("radio")).toHaveLength(3);

    await user.type(within(form).getByLabelText("Amount that came in"), "2,750,000");
    await user.click(within(form).getByRole("button", { name: "Find payouts" }));
    expect(listReturnable).toHaveBeenLastCalledWith({ minor: 2_750_000, currency: "UGX" });
    const matching = await within(form).findAllByRole("radio");
    expect(matching).toHaveLength(2);
    expect(form).toHaveTextContent("Centenary Bank •••• 4417");

    await user.click(within(form).getByRole("radio", { name: /Centenary Bank/ }));
    const line = await within(form).findByRole("radio", { name: /RTN 0413/ });
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose the statement line");
    await user.click(line);
    await user.type(within(form).getByLabelText("Why it came back"), "Account closed");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(propose).toHaveBeenCalledWith("key-1", "-OdBankPay00000000P3", "-OdTrustLine00000052", "Account closed");
    expect(await screen.findByText("Proposed. A different person must now approve it.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Returns waiting for a second person" })).getAllByRole("button")).toHaveLength(3);
    // Proposed, it is no longer offered.
    expect(await screen.findByRole("group", { name: "The payout that came back" })).not.toHaveTextContent("Centenary Bank");
  });

  it("says when no payout matches the amount, and refuses an amount it cannot read", async () => {
    const user = renderSection();
    const form = screen.getByRole("form", { name: "Record a payout that came back" });
    await within(form).findByRole("group", { name: "The payout that came back" });
    await user.type(within(form).getByLabelText("Amount that came in"), "12.5");
    await user.click(within(form).getByRole("button", { name: "Find payouts" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Enter whole shillings");
    await user.clear(within(form).getByLabelText("Amount that came in"));
    await user.type(within(form).getByLabelText("Amount that came in"), "999");
    await user.click(within(form).getByRole("button", { name: "Find payouts" }));
    expect(await within(form).findByText(/No payout paid in the last 120 days was for UGX 999/)).toBeInTheDocument();
  });

  it("says when no line coming in could be the payout", async () => {
    const user = renderSection();
    const form = screen.getByRole("form", { name: "Record a payout that came back" });
    await user.click(await within(form).findByRole("radio", { name: /Stanbic Bank/ }));
    expect(await within(form).findByText(/Import that statement first/)).toBeInTheDocument();
  });

  it("lets a second person approve, and it leaves the list", async () => {
    const api = createStubReturnsApi(ME, 0);
    const approve = vi.spyOn(api, "approve");
    const user = renderSection(api);
    const card = await screen.findByRole("article", { name: "Return of payout -OdBankPay00000000P1" });
    expect(card).toHaveTextContent("A colleague proposed");
    await user.type(within(card).getByLabelText("Your reason"), "Opened the line");
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    expect(approve).toHaveBeenCalledWith("key-1", "-OdReturn00000000001", "Opened the line");
    expect(await screen.findByText(/Taken back/)).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Returns waiting for a second person" })).getAllByRole("button")).toHaveLength(1);
  });

  it("offers the proposer only to withdraw", async () => {
    const user = renderSection();
    const list = await screen.findByRole("list", { name: "Returns waiting for a second person" });
    await user.click(within(list).getByRole("button", { name: /-OdMobPay000000000P2/ }));
    const card = screen.getByRole("article", { name: "Return of payout -OdMobPay000000000P2" });
    expect(within(card).queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
    await user.type(within(card).getByLabelText("Why you are withdrawing it"), "Wrong line");
    await user.click(within(card).getByRole("button", { name: "Withdraw" }));
    expect(await screen.findByText("Withdrawn. The payout stays paid.")).toBeInTheDocument();
  });

  it("words a refusal from Finvest", async () => {
    const api = createStubReturnsApi(ME, 0);
    vi.spyOn(api, "propose").mockRejectedValueOnce(new ReturnsError("return_already_recorded"));
    const user = renderSection(api);
    const form = screen.getByRole("form", { name: "Record a payout that came back" });
    await user.click(await within(form).findByRole("radio", { name: /Airtel Money/ }));
    await user.click(await within(form).findByRole("radio", { name: /REV 99231/ }));
    await user.type(within(form).getByLabelText("Why it came back"), "Again");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("A payout comes back once.");
  });

  it("shows nothing to someone who cannot take back a payout, and an empty list", async () => {
    const { unmount } = render(<Returns api={createStubReturnsApi(ME, 0)} me={ME} canChange={false} />);
    expect(screen.queryByRole("heading", { name: "Payouts that came back" })).not.toBeInTheDocument();
    unmount();
    renderSection(createStubReturnsApi(ME, 0, "empty"));
    expect(await screen.findByText("No return is waiting for a second person.")).toBeInTheDocument();
  });
});
