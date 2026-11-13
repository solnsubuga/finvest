import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { notConnectedAuditApi, type AuditApi, type AuditExport } from "./api.ts";
import { ReportsScreen } from "./index.tsx";
import { createStubAuditApi } from "./stub.ts";

const ME = { id: "-NstubMe000000000000", name: "Solomon N." };
const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderReports(api: AuditApi = createStubAuditApi(ME, () => NOW), canReadAudit = true) {
  const save = vi.fn<(exported: AuditExport) => void>();
  render(<ReportsScreen api={api} canReadAudit={canReadAudit} save={save} />);
  return { api, save, user: userEvent.setup() };
}

// jsdom's Blob has no text(), so read it the way a browser always could.
function readText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("unreadable"));
    reader.readAsText(file);
  });
}

const events = () => screen.findByRole("list", { name: "Events" });
const rows = async () => within(await events()).getAllByRole("listitem");

describe("reports and audit", () => {
  it("lists the trail newest first and opens an event's details", async () => {
    const { user } = renderReports();
    expect(await rows()).toHaveLength(50);
    const first = (await rows())[0] as HTMLElement;
    expect(within(first).getByText("Grace Auma")).toBeInTheDocument();
    expect(within(first).getByText("identity.team.change_proposed")).toBeInTheDocument();
    await user.click(within(first).getByRole("button", { expanded: false }));
    expect(within(first).getByText("Moving to the operations desk")).toBeInTheDocument();
    expect(within(first).getByText("change_role")).toBeInTheDocument();
    expect(screen.getByText(/50 events shown, older ones follow/)).toBeInTheDocument();
  });

  it("shows older events after the first page", async () => {
    const { user } = renderReports();
    await events();
    await user.click(screen.getByRole("button", { name: "Show older events" }));
    expect(await screen.findByText(/60 events shown\. That is all of them/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show older events" })).not.toBeInTheDocument();
  });

  it("filters by outcome, and says when nothing matches", async () => {
    const { user } = renderReports();
    await events();
    await user.selectOptions(screen.getByLabelText("Outcome"), "denied");
    await user.click(screen.getByRole("button", { name: "Show events" }));
    const denied = await rows();
    expect(denied).toHaveLength(5);
    for (const row of denied) expect(within(row).getByText("Denied")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Record ID"), "-NstubNothing");
    await user.click(screen.getByRole("button", { name: "Show events" }));
    expect(await screen.findByRole("heading", { name: "No events match" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await rows()).toHaveLength(50);
  });

  it("narrows to everything in one request from an event", async () => {
    const { user } = renderReports();
    const first = (await rows())[0] as HTMLElement;
    await user.click(within(first).getByRole("button", { expanded: false }));
    await user.click(within(first).getByRole("button", { name: "Everything in this request" }));
    expect(await rows()).toHaveLength(1);
    expect(screen.getByLabelText("Request ID")).toHaveValue("req_stub0060");
  });

  it("exports the filtered trail, and says so when it is too large", async () => {
    const { user, save } = renderReports();
    await events();
    await user.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText(/Exported\. The file holds every event/)).toBeInTheDocument();
    const exported = save.mock.calls[0]?.[0];
    expect(exported?.filename).toBe("finvest-audit-20261113-120000.csv");
    const text = exported ? await readText(exported.file) : "";
    expect(text.split("\r\n")[0]).toBe("occurred_at,category,action,outcome,actor_kind,actor_id,actor_name,resource_type,resource_id,reason,correlation_id,details");

    const busy = renderReports(createStubAuditApi(ME, () => NOW, { exportLimit: 20 }));
    await screen.findAllByRole("list", { name: "Events" });
    await busy.user.click(screen.getAllByRole("button", { name: "Export CSV" })[1] as HTMLElement);
    expect(await screen.findByText(/More than 10,000 events match/)).toBeInTheDocument();
    expect(busy.save).not.toHaveBeenCalled();
  });

  it("refuses a to date before the from date", async () => {
    const { user } = renderReports();
    await events();
    await user.type(screen.getByLabelText("From"), "2026-11-13");
    await user.type(screen.getByLabelText("To"), "2026-11-10");
    await user.click(screen.getByRole("button", { name: "Show events" }));
    expect(await screen.findByText("The to date must be on or after the from date.")).toBeInTheDocument();
  });

  it("says when the trail cannot be loaded, and asks nothing without audit.view", async () => {
    renderReports(notConnectedAuditApi);
    expect(await screen.findByRole("heading", { name: "The audit trail could not be loaded" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();

    const events = vi.fn(notConnectedAuditApi.events);
    renderReports({ ...notConnectedAuditApi, events }, false);
    expect(screen.getByText(/Your role does not read the audit trail/)).toBeInTheDocument();
    expect(events).not.toHaveBeenCalled();
  });
});
