import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MigrationError, notConnectedMigrationApi, type MigrationApi, type Overview } from "./api.ts";
import { MigrationScreen } from "./index.tsx";
import { difference, differenceSummary, formatAmount, formatRate, formatWithCurrency, lineResult, parseAmount, parseCount, problemLabel } from "./labels.ts";
import { createStubMigrationApi } from "./stub.ts";

const NOW = () => new Date("2026-11-13T12:00:00Z");

function renderScreen(api: MigrationApi = createStubMigrationApi(0)) {
  render(<MigrationScreen api={api} now={NOW} />);
  return { api, user: userEvent.setup() };
}

const csv = (name: string) => new File(["legacy_client_ref\r\n"], name, { type: "text/csv" });

async function fillTrial(user: ReturnType<typeof userEvent.setup>, asOf = "2026-11-12") {
  await user.click(await screen.findByRole("button", { name: "Run a trial import" }));
  const form = screen.getByRole("form", { name: "Run a trial import" });
  await user.upload(within(form).getByLabelText("Clients sheet (CSV)"), csv("clients.csv"));
  await user.upload(within(form).getByLabelText("Investments sheet (CSV)"), csv("investments.csv"));
  await user.type(within(form).getByLabelText("Figures are as of"), asOf);
  return form;
}

describe("migration screen", () => {
  it("compares Finvest 1.0 with the latest trial, line by line", async () => {
    renderScreen();
    const table = await screen.findByRole("table", { name: "Finvest 1.0 totals compared with the trial import" });
    const row = (name: string) => within(within(table).getByRole("row", { name: new RegExp(`^${name}`) }));
    expect(row("Number of clients").getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["1,912", "1,909", "3", "Look into"]);
    expect(row("Pending orders").getByText("Agrees")).toBeInTheDocument();
    expect(row("Money invested, UGX").getByText("27,500,000")).toBeInTheDocument();
    expect(row("Money invested, USD").getAllByText("254,500.00")).toHaveLength(2);
    const signed = row("Interest earned so far, before tax, USD");
    expect(signed.getByText("Signed off")).toBeInTheDocument();
    expect(signed.getByText("Finvest 1.0 rounded each day's dollar interest up.")).toBeInTheDocument();
    expect(screen.getByText("1,909 of 1,912")).toBeInTheDocument();
    expect(screen.getByText("3 missing")).toBeInTheDocument();
    expect(screen.getByText("Differs by UGX 1,240")).toBeInTheDocument();
  });

  it("lists every problem with its sheet, row and column, and filters them", async () => {
    const { user } = renderScreen();
    const section = within(await screen.findByRole("region", { name: "Problems in the latest trial" }));
    expect(section.getByText("Figures as of 12 Nov 2026. Run by Sarah Operations on 13 Nov, 18:40.")).toBeInTheDocument();
    const list = within(section.getByRole("list", { name: "Problems found by the latest trial import" }));
    expect(list.getAllByRole("listitem")).toHaveLength(10);
    expect(list.getAllByRole("listitem")[0]).toHaveTextContent("ClientsRow 214phoneAnother client has the same phone numberError");
    expect(section.getByText("Showing the first 10 of 118. Fix these and run the trial again to see the rest.")).toBeInTheDocument();

    await user.click(section.getByRole("button", { name: "Warnings" }));
    expect(list.getAllByRole("listitem")).toHaveLength(4);
    expect(section.queryByText("Error")).not.toBeInTheDocument();
    expect(section.getByRole("button", { name: "Warnings" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows what differs, what still blocks the switch, and that nobody is invited yet", async () => {
    renderScreen();
    const differs = within(await screen.findByRole("list", { name: "Differences to sign off" }));
    const titles = differs.getAllByRole("listitem").map((item) => item.querySelector("span")?.textContent);
    expect(titles).toEqual([
      "Number of clients ",
      "Verified identities ",
      "Running investments ",
      "Money invested, UGX ",
      "Interest earned so far, before tax, UGX ",
      "INV-20418 row 418",
      "INV-20977 row 977",
      "INV-19034 row 34",
    ]);
    expect(differs.getByText("Finvest 1.0 shows 3 more")).toBeInTheDocument();
    expect(differs.getByText("Finvest 1.0 shows UGX 27,500,000 more")).toBeInTheDocument();
    expect(differs.getByText("Tax of 10% stated, the product withholds 15%")).toBeInTheDocument();
    expect(screen.getByText("Signed off by two people").nextSibling).toHaveTextContent("2");
    const blockers = within(screen.getByRole("list", { name: "Still to do before the switch" }));
    expect(blockers.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Fix every error in the spreadsheet",
      "Finance signs off every difference with a reason",
    ]);
    expect(screen.getByText("Nobody has been invited. Invitations go out by SMS and email after the switch.")).toBeInTheDocument();
  });

  it("shows claim progress once clients are invited", async () => {
    const base = await createStubMigrationApi(0).getOverview();
    const overview: Overview = {
      ...base,
      blockers: [],
      claims: { invited: 1912, claimedAndConfirmed: 1204, saidSomethingIsWrong: 9, notYetClaimed: 699, cannotBeInvited: 5 },
    };
    renderScreen({ ...createStubMigrationApi(0), getOverview: () => Promise.resolve(overview) });
    expect(await screen.findByText("Claimed and confirmed")).toBeInTheDocument();
    expect(screen.getByText("1,204")).toBeInTheDocument();
    expect(screen.getByText("699")).toBeInTheDocument();
    expect(screen.getByText("Everything agrees. The switch can be scheduled.")).toBeInTheDocument();
  });

  it("starts empty and explains what to do", async () => {
    renderScreen(createStubMigrationApi(0, "empty"));
    expect(await screen.findByRole("heading", { name: "No trial import yet" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run a trial import" })).toBeInTheDocument();
  });

  it("asks for both sheets and the as of date before running", async () => {
    const api = createStubMigrationApi(0);
    const runTrial = vi.spyOn(api, "runTrial");
    const { user } = renderScreen(api);
    await user.click(await screen.findByRole("button", { name: "Run a trial import" }));
    const form = within(screen.getByRole("form", { name: "Run a trial import" }));
    await user.click(form.getByRole("button", { name: "Run the trial" }));
    expect(form.getByText("Choose the clients sheet.")).toBeInTheDocument();
    expect(form.getByText("Choose the investments sheet.")).toBeInTheDocument();
    expect(form.getByText("Give the day the Finvest 1.0 figures were taken.")).toBeInTheDocument();
    expect(form.getByLabelText("Clients sheet (CSV)")).toHaveAttribute("aria-invalid", "true");
    expect(runTrial).not.toHaveBeenCalled();
  });

  it("refuses a sheet that is not CSV and a day that has not happened", async () => {
    const api = createStubMigrationApi(0);
    const runTrial = vi.spyOn(api, "runTrial");
    const { user } = renderScreen(api);
    await user.click(await screen.findByRole("button", { name: "Run a trial import" }));
    const form = within(screen.getByRole("form", { name: "Run a trial import" }));
    await userEvent.setup({ applyAccept: false }).upload(form.getByLabelText("Clients sheet (CSV)"), new File(["x"], "clients.xlsx"));
    await user.upload(form.getByLabelText("Investments sheet (CSV)"), csv("investments.csv"));
    await user.type(form.getByLabelText("Figures are as of"), "2026-11-14");
    await user.click(form.getByRole("button", { name: "Run the trial" }));
    expect(form.getByText("Save the sheet as CSV first.")).toBeInTheDocument();
    expect(form.getByText("This day has not happened yet.")).toBeInTheDocument();
    expect(runTrial).not.toHaveBeenCalled();
  });

  it("runs a trial and shows its result", async () => {
    const api = createStubMigrationApi(0);
    const runTrial = vi.spyOn(api, "runTrial");
    const { user } = renderScreen(api);
    const form = await fillTrial(user);
    await user.click(within(form).getByRole("button", { name: "Run the trial" }));

    expect(await screen.findByText("No errors")).toBeInTheDocument();
    expect(runTrial).toHaveBeenCalledWith(expect.objectContaining({ asOf: "2026-11-12" }));
    expect(runTrial.mock.calls[0]![0].clients.name).toBe("clients.csv");
    expect(screen.queryByRole("form", { name: "Run a trial import" })).not.toBeInTheDocument();
    expect(screen.getByText("1,912 of 1,912")).toBeInTheDocument();
    const blockers = within(screen.getByRole("list", { name: "Still to do before the switch" }));
    expect(blockers.getAllByRole("listitem")).toHaveLength(1);
  });

  it("keeps the form open with the reason when a trial fails", async () => {
    const api = createStubMigrationApi(0);
    vi.spyOn(api, "runTrial").mockRejectedValue(new MigrationError("file_too_large"));
    const { user } = renderScreen(api);
    const form = await fillTrial(user);
    await user.click(within(form).getByRole("button", { name: "Run the trial" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("One of the files is too large. A sheet holds at most 100,000 rows.");
  });

  it("hides the trial button from someone who may only look", async () => {
    const base = await createStubMigrationApi(0).getOverview();
    const overview: Overview = { ...base, allowed: [] };
    renderScreen({ ...createStubMigrationApi(0), getOverview: () => Promise.resolve(overview) });
    await screen.findByRole("table", { name: "Finvest 1.0 totals compared with the trial import" });
    expect(screen.queryByRole("button", { name: "Run a trial import" })).not.toBeInTheDocument();
  });

  it("says so when the migration cannot be loaded, and tries again", async () => {
    const api = createStubMigrationApi(0);
    const getOverview = vi.spyOn(api, "getOverview").mockRejectedValueOnce(new Error("offline"));
    const { user } = renderScreen(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest. Check your connection and try again.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("1,909 of 1,912")).toBeInTheDocument();
    expect(getOverview).toHaveBeenCalledTimes(2);
  });

  it("says it is not connected in a build with no backend client", async () => {
    renderScreen(notConnectedMigrationApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("Migration is not connected to the Finvest service yet.");
  });
});

describe("finance checks on the migration screen", () => {
  async function openTotals(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: "Enter them again" }));
    return within(screen.getByRole("form", { name: "Enter the Finvest 1.0 totals" }));
  }

  async function fillTotals(form: ReturnType<typeof within>, user: ReturnType<typeof userEvent.setup>) {
    await user.type(form.getByLabelText("Number of clients"), "1,909");
    await user.type(form.getByLabelText("Verified identities"), "1802");
    await user.type(form.getByLabelText("Running investments"), "3201");
    await user.type(form.getByLabelText("Pending orders"), "14");
    const [investedUgx, interestUgx] = form.getAllByLabelText("Shillings");
    const [investedUsd, interestUsd] = form.getAllByLabelText("Dollars");
    await user.type(investedUgx!, "18,212,500,000");
    await user.type(investedUsd!, "254,500");
    await user.type(interestUgx!, "420100000");
    await user.type(interestUsd!, "612.7");
  }

  it("enters the Finvest 1.0 totals, which wait for a second person before they count", async () => {
    const api = createStubMigrationApi(0);
    const enterTotals = vi.spyOn(api, "enterTotals");
    const { user } = renderScreen(api);
    const form = await openTotals(user);
    await fillTotals(form, user);
    await user.click(form.getByRole("button", { name: "Save for checking" }));

    expect(enterTotals).toHaveBeenCalledWith({
      asOf: "2026-11-12",
      clients: "1909",
      verifiedIdentities: "1802",
      runningInvestments: "3201",
      pendingOrders: "14",
      invested: { UGX: "18212500000", USD: "25450000" },
      interestBeforeTax: { UGX: "420100000", USD: "61270" },
    });
    const panel = within((await screen.findByRole("heading", { name: "Finvest 1.0 totals" })).parentElement!);
    expect(panel.getByText(/Entered by You on/)).toHaveTextContent("Someone else checks them against the report before they count.");
    expect(panel.queryByRole("button", { name: "Confirm these totals" })).not.toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Finvest 1.0 totals compared with the trial import" });
    expect(within(table).getAllByText("Not entered")).toHaveLength(8);
    const blockers = within(screen.getByRole("list", { name: "Still to do before the switch" }));
    expect(blockers.getByText("A second person confirms the Finvest 1.0 totals")).toBeInTheDocument();
  });

  it("refuses a total that is not a whole number in the smallest unit", async () => {
    const api = createStubMigrationApi(0);
    const enterTotals = vi.spyOn(api, "enterTotals");
    const { user } = renderScreen(api);
    const form = await openTotals(user);
    await fillTotals(form, user);
    await user.clear(form.getByLabelText("Number of clients"));
    await user.type(form.getByLabelText("Number of clients"), "1,912.5");
    await user.clear(form.getAllByLabelText("Dollars")[1]!);
    await user.type(form.getAllByLabelText("Dollars")[1]!, "612.755");
    await user.type(form.getAllByLabelText("Shillings")[0]!, ".50");
    await user.click(form.getByRole("button", { name: "Save for checking" }));
    expect(form.getByText("Write a whole number, such as 1,912.")).toBeInTheDocument();
    expect(form.getByText("Write dollars with at most two decimals.")).toBeInTheDocument();
    expect(form.getByText("Write whole shillings, such as 18,240,000,000.")).toBeInTheDocument();
    expect(enterTotals).not.toHaveBeenCalled();
  });

  it("confirms totals a colleague entered, and then compares them", async () => {
    const { user } = renderScreen(createStubMigrationApi(0, "unconfirmed"));
    const confirm = await screen.findByRole("button", { name: "Confirm these totals" });
    expect(screen.getByText(/Entered by Grace Finance/)).toHaveTextContent("Check each figure against Finvest 1.0's report before confirming.");
    await user.click(confirm);
    expect(await screen.findByText(/Checked by You\./)).toBeInTheDocument();
    expect(screen.getByText("1,909 of 1,912")).toBeInTheDocument();
  });

  it("proposes a sign-off with a reason, which the proposer cannot approve", async () => {
    const api = createStubMigrationApi(0);
    const propose = vi.spyOn(api, "proposeSignOff");
    const { user } = renderScreen(api);
    const list = within(await screen.findByRole("list", { name: "Differences to sign off" }));
    const row = within(list.getByText("INV-20418").closest("li")!);
    await user.click(row.getByRole("button", { name: "Sign off" }));
    await user.click(row.getByRole("button", { name: "Propose the sign-off" }));
    expect(row.getByText("Say why, in a sentence someone else can check.")).toBeInTheDocument();
    await user.type(row.getByLabelText("Why the difference on INV-20418 is right"), "  Counted the start date  ");
    await user.click(row.getByRole("button", { name: "Propose the sign-off" }));

    expect(propose).toHaveBeenCalledWith("investment:INV-20418", "Counted the start date");
    const after = within((await screen.findByText("Proposed by you:")).closest("li")!);
    expect(after.getByText("Someone else approves it.")).toBeInTheDocument();
    expect(after.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText("Waiting for approval").nextSibling).toHaveTextContent("2");
  });

  it("approves a colleague's sign-off, and a rejection needs a reason", async () => {
    const api = createStubMigrationApi(0);
    const decide = vi.spyOn(api, "decideSignOff");
    const { user } = renderScreen(api);
    const list = within(await screen.findByRole("list", { name: "Differences to sign off" }));
    const row = within(list.getByText("INV-20977").closest("li")!);
    expect(row.getByText("Proposed by Grace Finance:")).toBeInTheDocument();
    await user.click(row.getByRole("button", { name: "Reject" }));
    await user.click(row.getByRole("button", { name: "Reject" }));
    expect(decide).not.toHaveBeenCalled();
    await user.click(row.getByRole("button", { name: "Cancel" }));
    await user.click(row.getByRole("button", { name: "Approve" }));

    expect(decide).toHaveBeenCalledWith("sign-off-20977", true, undefined);
    await waitFor(() => expect(screen.queryByText("INV-20977")).not.toBeInTheDocument());
    expect(screen.getByText("Signed off by two people").nextSibling).toHaveTextContent("3");
  });

  it("shows the latest figures when someone else changed them first", async () => {
    const api = createStubMigrationApi(0);
    vi.spyOn(api, "decideSignOff").mockRejectedValue(new MigrationError("conflict"));
    const getOverview = vi.spyOn(api, "getOverview");
    const { user } = renderScreen(api);
    const list = within(await screen.findByRole("list", { name: "Differences to sign off" }));
    await user.click(within(list.getByText("INV-20977").closest("li")!).getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Someone changed this while you were looking. The screen now shows the latest figures.");
    expect(getOverview).toHaveBeenCalledTimes(2);
  });

  it("offers nothing to change to someone who may only look", async () => {
    renderScreen(createStubMigrationApi(0, "unconfirmed", []));
    await screen.findByRole("list", { name: "Differences to sign off" });
    for (const name of ["Run a trial import", "Confirm these totals", "Enter them again", "Sign off", "Approve", "Reject"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("refuses a sheet larger than the backend accepts before uploading it", async () => {
    const api = createStubMigrationApi(0);
    const runTrial = vi.spyOn(api, "runTrial");
    const { user } = renderScreen(api);
    await user.click(await screen.findByRole("button", { name: "Run a trial import" }));
    const form = within(screen.getByRole("form", { name: "Run a trial import" }));
    const huge = csv("clients.csv");
    Object.defineProperty(huge, "size", { value: 40_000_001 });
    await user.upload(form.getByLabelText("Clients sheet (CSV)"), huge);
    await user.upload(form.getByLabelText("Investments sheet (CSV)"), csv("investments.csv"));
    await user.type(form.getByLabelText("Figures are as of"), "2026-11-12");
    await user.click(form.getByRole("button", { name: "Run the trial" }));
    expect(form.getByText("This sheet is larger than 40 MB. Split the export or remove columns that are not in the format.")).toBeInTheDocument();
    expect(runTrial).not.toHaveBeenCalled();
  });
});

describe("migration labels", () => {
  it("formats amounts from whole-number strings without floats", () => {
    expect(formatAmount("18240000000", "UGX")).toBe("18,240,000,000");
    expect(formatAmount("25450000", "USD")).toBe("254,500.00");
    expect(formatAmount("5", "USD")).toBe("0.05");
    expect(formatAmount("-1240", "UGX")).toBe("-1,240");
    expect(formatAmount("9007199254740993", null)).toBe("9,007,199,254,740,993");
    expect(formatWithCurrency("61275", "USD")).toBe("$612.75");
    expect(difference("9007199254740993", "9007199254740992")).toBe("1");
  });

  it("reads typed totals into the smallest unit without floats", () => {
    expect(parseCount("1,912")).toBe("1912");
    expect(parseCount(" 0 ")).toBe("0");
    for (const bad of ["", "1.5", "-1", "1e3", "12a", "9007199254740992"]) expect(parseCount(bad)).toBeNull();
    expect(parseCount("9007199254740991")).toBe("9007199254740991");
    expect(parseAmount("18,240,000,000", "UGX")).toBe("18240000000");
    expect(parseAmount("612.7", "USD")).toBe("61270");
    expect(parseAmount("0.05", "USD")).toBe("5");
    expect(parseAmount("254,500", "USD")).toBe("25450000");
    for (const bad of ["1.5", "-3"]) expect(parseAmount(bad, "UGX")).toBeNull();
    for (const bad of ["1.555", "1.", ".5"]) expect(parseAmount(bad, "USD")).toBeNull();
  });

  it("compares a line as whole numbers, not as text", () => {
    const base = { key: "k", figure: "clients" as const, currency: null, signedOff: null };
    expect(lineResult({ ...base, old: "0100", new: "100" })).toBe("agrees");
    expect(lineResult({ ...base, old: "9007199254740993", new: "9007199254740992" })).toBe("look_into");
    const proposed = { id: "s", reason: "r", state: "proposed" as const, proposedBy: "A", approvedBy: null, mine: false };
    expect(lineResult({ ...base, old: "2", new: "1", signedOff: proposed })).toBe("waiting");
    expect(lineResult({ ...base, old: "2", new: "1", signedOff: { ...proposed, state: "approved", approvedBy: "B" } })).toBe("signed_off");
    expect(lineResult({ ...base, old: null, new: "1" })).toBe("not_entered");
  });

  it("formats basis points as a percentage", () => {
    expect(formatRate("1500")).toBe("15%");
    expect(formatRate("1250")).toBe("12.5%");
    expect(formatRate("725")).toBe("7.25%");
  });

  it("describes a difference in words", () => {
    const base = { key: "k", row: 1, legacyInvestmentRef: "A", currency: "UGX" as const, signedOff: null };
    expect(differenceSummary({ ...base, kind: "interest", old: "100", new: "150" })).toBe("Finvest 1.0 shows UGX 50 less interest");
    expect(differenceSummary({ ...base, kind: "interest", old: "100", new: "100" })).toBe("The tax held differs");
  });

  it("still says something for a problem code it does not know", () => {
    expect(problemLabel({ sheet: "clients", row: 2, column: null, code: "brand_new", severity: "error" })).toBe("Problem “brand_new”");
  });
});
