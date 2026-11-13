import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { STUB_ROLE_PERMISSIONS } from "@/auth/stub.ts";
import type { Role } from "@/auth/types.ts";
import { createHttpApprovalsApi, notConnectedApprovalsApi, type ApprovalsApi } from "./api.ts";
import { OverviewScreen } from "./index.tsx";
import { createStubApprovalsApi } from "./stub.ts";

const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderOverview(api: ApprovalsApi) {
  render(
    <MemoryRouter>
      <OverviewScreen api={api} now={() => new Date(NOW)} />
    </MemoryRouter>,
  );
}

const as = (role: Role, sample: "sample" | "empty" = "sample") =>
  createStubApprovalsApi({ id: "stub-me", name: "Solomon N.", permissions: STUB_ROLE_PERMISSIONS[role] }, () => NOW, sample);

describe("overview inbox", () => {
  it("splits what an admin may decide from what they proposed, with links to each screen", async () => {
    renderOverview(as("admin"));
    const decide = await screen.findByRole("list", { name: "For you to decide" });
    const lock = within(decide).getByText("Lock client -NstubClient00000042").closest("li") as HTMLElement;
    expect(lock).toHaveTextContent(/Proposed by Grace Auma, 55 minutes ago/);
    expect(within(lock).getByRole("link", { name: /Review/ })).toHaveAttribute("href", "/clients?client=-NstubClient00000042");
    const team = within(decide).getByText("Role change").closest("li") as HTMLElement;
    expect(within(team).getByRole("link")).toHaveAttribute("href", "/team");
    const mine = screen.getByRole("list", { name: "Yours, waiting for someone else" });
    expect(within(mine).getByText("Christmas opening hours")).toBeInTheDocument();
    expect(mine).toHaveTextContent(/Proposed by you/);
    expect(within(mine).getByRole("link", { name: /Open/ })).toHaveAttribute("href", "/settings");
  });

  it("shows each role only what it may see, and what it cannot decide apart", async () => {
    renderOverview(as("support"));
    const decide = await screen.findByRole("list", { name: "For you to decide" });
    expect(within(decide).getByText("Address d356ebd92915…")).toBeInTheDocument();
    const others = screen.getByRole("list", { name: "Waiting for another role" });
    expect(within(others).getByText("Lock client -NstubClient00000042")).toBeInTheDocument();
    expect(screen.queryByText("TB364")).not.toBeInTheDocument();
    expect(screen.getByText("You have nothing waiting.")).toBeInTheDocument();
  });

  it("says when nothing is waiting, and when the inbox cannot be loaded", async () => {
    renderOverview(as("admin", "empty"));
    expect(await screen.findByRole("heading", { name: "Nothing is waiting" })).toBeInTheDocument();
    renderOverview(notConnectedApprovalsApi);
    expect(await screen.findByRole("heading", { name: "What is waiting could not be loaded" })).toBeInTheDocument();
  });

  it("reads the inbox from the API and keeps a kind it does not know, without a link", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            items: [
              { kind: "rate_change", id: "a", subject: "TB91", proposed_by: "s1", proposed_by_name: "Ruth Namata", proposed_at: "2026-11-13T08:00:00Z", yours: false, can_decide: true },
              { kind: "future_kind", id: "b", subject: "x", proposed_by: "s2", proposed_by_name: null, proposed_at: "2026-11-13T08:30:00Z", yours: false, can_decide: true },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    renderOverview(createHttpApprovalsApi(createHttp("https://api.test", fetch)));
    const decide = await screen.findByRole("list", { name: "For you to decide" });
    expect(fetch).toHaveBeenCalledWith("https://api.test/staff/approvals", expect.objectContaining({ method: "GET" }));
    const rows = within(decide).getAllByRole("listitem");
    expect(within(rows[0] as HTMLElement).getByRole("link")).toHaveAttribute("href", "/rates");
    expect(within(rows[1] as HTMLElement).getByText("Approval")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).queryByRole("link")).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent(/Proposed by a member of staff/);
  });
});
