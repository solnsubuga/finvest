import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { type ReturnsApi } from "./api.ts";
import { Returns } from "./Returns.tsx";
import { createStubReturnsApi } from "./stub.ts";

const ME = "-Nstaff000000000000A";

function renderSection(api: ReturnsApi = createStubReturnsApi(ME, 0), canChange = true) {
  let keys = 0;
  render(<Returns api={api} me={ME} canChange={canChange} newKey={() => `key-${++keys}`} />);
  return userEvent.setup();
}

describe("payouts that came back", () => {
  it("proposes a return with the statement line and a reason", async () => {
    const api = createStubReturnsApi(ME, 0);
    const propose = vi.spyOn(api, "propose");
    const user = renderSection(api);
    await screen.findByRole("list", { name: "Returns waiting for a second person" });
    const form = screen.getByRole("form", { name: "Record a payout that came back" });
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Enter the payout ID.");
    await user.type(within(form).getByLabelText("Payout ID"), "-OdBankPay00000000P9");
    await user.type(within(form).getByLabelText("Statement line ID"), "-OdTrustLine00000099");
    await user.type(within(form).getByLabelText("Why it came back"), "Account closed");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(propose).toHaveBeenCalledWith("key-1", "-OdBankPay00000000P9", "-OdTrustLine00000099", "Account closed");
    expect(await screen.findByText("Proposed. A different person must now approve it.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Returns waiting for a second person" })).getAllByRole("button")).toHaveLength(3);
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
    const user = renderSection();
    await screen.findByRole("list", { name: "Returns waiting for a second person" });
    const form = screen.getByRole("form", { name: "Record a payout that came back" });
    await user.type(within(form).getByLabelText("Payout ID"), "-OdBankPay00000000P1");
    await user.type(within(form).getByLabelText("Statement line ID"), "-OdTrustLine00000098");
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
