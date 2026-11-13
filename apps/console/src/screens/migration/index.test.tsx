import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MigrationError, notConnectedMigrationApi, type MigrationApi, type Overview } from "./api.ts";
import { MigrationScreen } from "./index.tsx";
import { difference, differenceSummary, formatAmount, formatRate, formatWithCurrency, problemLabel } from "./labels.ts";
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
    const differs = within(await screen.findByRole("list", { name: "Investments that differ" }));
    expect(differs.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "INV-20418 row 418Finvest 1.0 shows UGX 620 more interest",
      "INV-20977 row 977Finvest 1.0 shows UGX 620 more interest",
      "INV-19034 row 34Tax of 10% stated, the product withholds 15%",
    ]);
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
    renderScreen({ getOverview: () => Promise.resolve(overview), runTrial: () => Promise.resolve(overview) });
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
    renderScreen({ getOverview: () => Promise.resolve(overview), runTrial: () => Promise.resolve(overview) });
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
