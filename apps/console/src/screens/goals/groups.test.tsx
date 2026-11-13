import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { GoalsError } from "./api.ts";
import { notConnectedGroupsApi, type GroupsApi } from "./groupsApi.ts";
import { createStubGroupsApi, sampleGroups } from "./groupsStub.ts";
import { GoalsScreen } from "./index.tsx";
import { createStubGoalsApi } from "./stub.ts";

const TODAY = "2026-11-13";

async function openGroups(groupsApi: GroupsApi = createStubGroupsApi(TODAY, 0)) {
  const user = userEvent.setup();
  render(<GoalsScreen api={createStubGoalsApi(TODAY, 0)} groupsApi={groupsApi} />);
  await user.click(screen.getByRole("button", { name: "Group goals" }));
  return user;
}

const rows = () => within(screen.getByRole("table", { name: "Group goals" })).getAllByRole("row").slice(1);

describe("group goals in the console", () => {
  it("lists group goals newest first and filters them", async () => {
    const user = await openGroups();
    expect(await screen.findByText("Open group goals")).toBeInTheDocument();
    expect(rows()).toHaveLength(3);
    expect(rows()[0]).toHaveTextContent("Wedding in Mbarara");
    await user.click(screen.getByRole("button", { name: "Closed" }));
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toHaveTextContent("Office for our shop");
    expect(rows()[0]).toHaveTextContent("On 7 Nov 2026");
  });

  it("shows the combined total and each member's own figure, with those who left apart", async () => {
    const user = await openGroups();
    await user.click(await screen.findByRole("button", { name: "Family land in Mukono" }));
    const detail = await screen.findByRole("region", { name: "Group goal: Family land in Mukono" });
    expect(within(detail).getByText("UGX 2,800,000")).toBeInTheDocument();
    expect(within(detail).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "23");
    expect(within(detail).getByText("Members now").nextSibling).toHaveTextContent("3");
    expect(within(detail).getByText("UGX 1,850,000")).toBeInTheDocument();
    expect(within(detail).getByText(", set it up")).toBeInTheDocument();
    const left = within(detail).getByRole("heading", { name: "Left the group" }).parentElement as HTMLElement;
    expect(within(left).getByText("Joel")).toBeInTheDocument();
    expect(within(left).getByText(/left 1 Nov 2026/)).toBeInTheDocument();
  });

  it("says when a new member has not been counted yet", async () => {
    const user = await openGroups();
    await user.click(await screen.findByRole("button", { name: "Wedding in Mbarara" }));
    const detail = await screen.findByRole("region", { name: "Group goal: Wedding in Mbarara" });
    expect(within(detail).getByText("Not counted yet")).toBeInTheDocument();
    expect(within(detail).getByText("Counted as of").nextSibling).toHaveTextContent("12 Nov 2026");
  });

  it("has no control that changes a group", async () => {
    const user = await openGroups();
    await user.click(await screen.findByRole("button", { name: "Family land in Mukono" }));
    await screen.findByRole("region", { name: "Group goal: Family land in Mukono" });
    const labels = screen.getAllByRole("button").map((button) => button.textContent);
    expect(labels).toEqual([
      "Goals",
      "Group goals",
      "All",
      "Open",
      "Closed",
      ...sampleGroups(TODAY).map((detail) => detail.group.name),
    ]);
  });

  it("explains an empty list", async () => {
    await openGroups(createStubGroupsApi(TODAY, 0, "empty"));
    expect(await screen.findByText("No group goals yet")).toBeInTheDocument();
  });

  it("offers to try again when the list fails", async () => {
    const user = await openGroups(createStubGroupsApi(TODAY, 0, "error"));
    expect(await screen.findByText("Group goals could not be loaded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Goals" }));
    expect(await screen.findByText("Open goals")).toBeInTheDocument();
  });

  it("says plainly when the console is not connected", async () => {
    await openGroups(notConnectedGroupsApi);
    expect(await screen.findByText("The console is not connected to Finvest yet.")).toBeInTheDocument();
  });

  it("names a group that is gone", async () => {
    const api: GroupsApi = { ...createStubGroupsApi(TODAY, 0), getGroup: () => Promise.reject(new GoalsError("not_found")) };
    const user = await openGroups(api);
    await user.click(await screen.findByRole("button", { name: "Family land in Mukono" }));
    expect(await screen.findByText("This group goal could not be found.")).toBeInTheDocument();
  });
});
