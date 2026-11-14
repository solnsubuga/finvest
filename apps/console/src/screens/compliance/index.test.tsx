import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { notConnectedComplianceApi, type ComplianceApi } from "./api.ts";
import { ComplianceScreen } from "./index.tsx";
import { createStubComplianceApi, type StubCase } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const NOW = "2026-11-14T09:00:00Z";

function renderScreen({ sample = "default", api, canChange = true }: { sample?: StubCase; api?: ComplianceApi; canChange?: boolean } = {}) {
  render(<ComplianceScreen api={api ?? createStubComplianceApi(ME, sample, () => NOW)} me={ME.id} canChange={canChange} />);
  return userEvent.setup();
}

const open = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
  const table = await screen.findByRole("table", { name: "Compliance cases" });
  const row = within(table).getByRole("row", { name: new RegExp(name) });
  await user.click(within(row).getByRole("button"));
  return screen.findByRole("region", { name: new RegExp(`^Case: .*${name}`) });
};

describe("compliance", () => {
  it("shows the figures, open cases oldest first, and payouts held for screening", async () => {
    renderScreen();
    expect(await screen.findByText("Open cases")).toBeInTheDocument();
    expect(await screen.findByText("2 screening, 2 monitoring")).toBeInTheDocument();
    const rows = within(await screen.findByRole("table", { name: "Compliance cases" })).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[0]?.textContent)).toEqual([
      "Sarah NakatoFV 2034 5521",
      "Joseph OkelloFV 2034 7702",
      "Ruth AchiengFV 2035 0118",
      "Peter MugishaFV 2034 9930",
    ]);
    const payouts = await screen.findByRole("table", { name: "Payouts held for screening" });
    expect(payouts).toHaveTextContent("UGX 15,000,000");
    expect(payouts).toHaveTextContent("Being screened");
    expect(screen.queryByText(/Screening is off/)).toBeNull();
  });

  it("filters to monitoring cases", async () => {
    const user = renderScreen();
    await screen.findByRole("table", { name: "Compliance cases" });
    await user.click(screen.getByRole("button", { name: "Monitoring" }));
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Compliance cases" })).getAllByRole("row")).toHaveLength(3));
  });

  it("shows what a screen found, and approves someone else's proposal", async () => {
    const user = renderScreen();
    const view = await open(user, "Sarah Nakato");
    expect(view).toHaveTextContent("Sara Nakato");
    expect(view).toHaveTextContent("71% match");
    expect(view).toHaveTextContent("Not a match, proposed by Grace Auma");
    await user.click(within(view).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(view).toHaveTextContent("Closed as not a match"));
    expect(view).toHaveTextContent("Approved by Amina Kintu");
  });

  it("lets the proposer only withdraw their own", async () => {
    const user = renderScreen();
    const view = await open(user, "Ruth Achieng");
    expect(view).toHaveTextContent("Total in the window");
    expect(view).toHaveTextContent("UGX 21,500,000");
    expect(within(view).queryByRole("button", { name: "Approve" })).toBeNull();
    await user.click(within(view).getByRole("button", { name: "Withdraw it" }));
    await waitFor(() => expect(view).toHaveTextContent("Withdrawn by Amina Kintu"));
    expect(within(view).getByRole("form", { name: "Propose an outcome" })).toBeInTheDocument();
  });

  it("proposes only an outcome that fits, with a reason", async () => {
    const user = renderScreen();
    const view = await open(user, "Peter Mugisha");
    const form = within(view).getByRole("form", { name: "Propose an outcome" });
    expect(within(form).queryByLabelText(/Not a match/)).toBeNull();
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    expect(form).toHaveTextContent("Choose an outcome.");
    expect(form).toHaveTextContent("Write the reason.");
    await user.click(within(form).getByLabelText(/Report to the FIA/));
    await user.type(within(form).getByLabelText(/Why\?/), "Filed on goAML, reference STR-2026-0150");
    await user.click(within(form).getByRole("button", { name: "Propose" }));
    await waitFor(() => expect(view).toHaveTextContent("Report to the FIA, proposed by you"));
  });

  it("sends a proposal back only with a note", async () => {
    const user = renderScreen();
    const view = await open(user, "Sarah Nakato");
    await user.click(within(view).getByRole("button", { name: "Send back" }));
    const form = within(view).getByRole("form", { name: "Send the proposal back" });
    await user.click(within(form).getByRole("button", { name: "Send it back" }));
    expect(form).toHaveTextContent("Say why");
    await user.type(within(form).getByLabelText("What needs checking?"), "Attach the ID page");
    await user.click(within(form).getByRole("button", { name: "Send it back" }));
    await waitFor(() => expect(view).toHaveTextContent("Sent back by Amina Kintu"));
  });

  it("hides every change from someone who may only look", async () => {
    const user = renderScreen({ canChange: false });
    const view = await open(user, "Peter Mugisha");
    expect(within(view).queryByRole("form")).toBeNull();
    expect(view).toHaveTextContent("Someone with the compliance role proposes");
  });

  it("says when screening is off", async () => {
    renderScreen({ sample: "off" });
    expect(await screen.findByText(/Screening is off/)).toBeInTheDocument();
    expect(await screen.findByText("No open cases")).toBeInTheDocument();
    expect(await screen.findByText("No payout is waiting for a screen.")).toBeInTheDocument();
  });

  it("says when Finvest cannot be reached, and when it is not connected", async () => {
    renderScreen({ sample: "error" });
    expect(await screen.findByRole("heading", { name: "Compliance could not be loaded" })).toBeInTheDocument();
    expect(screen.getAllByText(/could not reach Finvest/).length).toBeGreaterThan(0);
  });

  it("says when it is not connected", async () => {
    renderScreen({ api: notConnectedComplianceApi });
    expect((await screen.findAllByText(/not connected to the Finvest service/)).length).toBeGreaterThan(0);
  });
});
