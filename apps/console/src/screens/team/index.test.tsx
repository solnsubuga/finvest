import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { notConnectedTeamApi, type TeamApi } from "./api.ts";
import { TeamScreen } from "./index.tsx";
import { createStubTeamApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Solomon N." };
const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderTeam(api: TeamApi = createStubTeamApi(ME, () => NOW)) {
  render(<TeamScreen api={api} me={ME.id} />);
  return { api, user: userEvent.setup() };
}

const change = (name: RegExp) => screen.findByRole("article", { name });
const person = (name: string) => within(screen.getByRole("list", { name: "People" })).getByRole("listitem", { name });

describe("team and permissions", () => {
  it("shows what is waiting, the people and what each role can do", async () => {
    renderTeam();
    const role = await change(/Move Amina Kintu from Support to Operations/);
    expect(within(role).getByText(/Proposed by Grace Auma/)).toBeInTheDocument();
    expect(within(role).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    const invite = await change(/Invite Brenda Achieng/);
    // Your own proposal can be withdrawn but never approved by you.
    expect(within(invite).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(within(invite).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
    expect(screen.getByText(/6 people with access, 1 deactivated/)).toBeInTheDocument();
    expect(within(person("Solomon N.")).queryByRole("button", { name: "Propose a change" })).not.toBeInTheDocument();
    expect(within(person("Amina Kintu")).getByText("Change waiting")).toBeInTheDocument();
    const matrix = screen.getByRole("table", { name: "What each role can open and do" });
    const teamRow = within(matrix).getByRole("row", { name: /Change the team/ });
    expect(within(teamRow).getAllByLabelText("Yes")).toHaveLength(1);
  });

  it("approves a change with a reason, and the person's role changes", async () => {
    const { user } = renderTeam();
    const card = await change(/Move Amina Kintu/);
    await user.click(within(card).getByRole("button", { name: "Approve" }));
    await user.click(within(card).getByRole("button", { name: "Approve the change" }));
    expect(within(card).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.type(within(card).getByLabelText(/^Why approve it\?/), "Agreed with Daniel.");
    await user.click(within(card).getByRole("button", { name: "Approve the change" }));
    expect(await screen.findByText("Approved. The change is in effect.")).toBeInTheDocument();
    expect(within(person("Amina Kintu")).getByText("Operations")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /Move Amina Kintu/ })).not.toBeInTheDocument();
  });

  it("proposes a deactivation and refuses leaving no admin", async () => {
    const { user, api } = renderTeam();
    await change(/Move Amina Kintu/);
    const row = person("Daniel Kato");
    await user.click(within(row).getByRole("button", { name: "Propose a change" }));
    const form = within(row).getByRole("form", { name: "Propose a change for Daniel Kato" });
    await user.click(within(form).getByLabelText("Deactivate"));
    await user.type(within(form).getByLabelText(/^Why\?/), "Left Finvest on Friday.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByText(/Another admin must approve it/)).toBeInTheDocument();
    expect(await change(/Deactivate Daniel Kato/)).toBeInTheDocument();
    // The stub keeps the last admin rule the backend has.
    await expect(api.propose({ kind: "deactivate", staffId: "sample-admin-2", reason: "x" })).resolves.toBeTruthy();
  });

  it("checks an invite before sending it", async () => {
    const { user } = renderTeam();
    await change(/Move Amina Kintu/);
    await user.click(screen.getByRole("button", { name: "Invite someone" }));
    const form = screen.getByRole("form", { name: "Invite someone" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText("Enter their name.")).toBeInTheDocument();
    expect(within(form).getByText(/Enter an email address/)).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/^Full name/), "Ivan Ssali");
    await user.type(within(form).getByLabelText(/^Work email/), "daniel@finvestug.com");
    await user.type(within(form).getByLabelText(/^Why\?/), "New hire.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Someone on the team already uses this email address.");
  });

  it("says when nothing is waiting", async () => {
    renderTeam(createStubTeamApi(ME, () => NOW, "quiet"));
    expect(await screen.findByText("Nothing is waiting.")).toBeInTheDocument();
  });

  it("offers to try again when the team cannot be loaded", async () => {
    renderTeam(notConnectedTeamApi);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The team could not be loaded");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("lets a role without team.change look but not change", async () => {
    const stub = createStubTeamApi(ME, () => NOW);
    const readOnly: TeamApi = { ...stub, load: async () => ({ ...(await stub.load()), canChange: false }) };
    renderTeam(readOnly);
    const card = await change(/Move Amina Kintu/);
    expect(within(card).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite someone" })).not.toBeInTheDocument();
    expect(screen.getByText(/Changing it needs an admin/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryAllByRole("button", { name: "Propose a change" })).toHaveLength(0));
  });

  it("shows Cloudflare Access for each person and lets an admin try a failed one again", async () => {
    const { user } = renderTeam(createStubTeamApi(ME, () => NOW, "access"));
    const ruth = await screen.findByRole("listitem", { name: "Ruth Namata" });
    expect(within(ruth).getByText("Access not updated")).toBeInTheDocument();
    expect(within(ruth).getByText(/refused the token/)).toBeInTheDocument();
    expect(within(person("Peter Okello")).getByText("Adding to Access")).toBeInTheDocument();
    expect(within(person("Amina Kintu")).getByText("Access sync is off")).toBeInTheDocument();
    expect(within(person("Daniel Kato")).getByRole("button", { name: "Check Access now" })).toBeInTheDocument();
    expect(within(person("Grace Auma")).getByText("Can reach the console")).toBeInTheDocument();
    await user.click(within(ruth).getByRole("button", { name: "Try Access again" }));
    expect(await screen.findByText(/Asked again/)).toBeInTheDocument();
    await waitFor(() => expect(within(person("Ruth Namata")).getByText("Can reach the console")).toBeInTheDocument());
  });
});
