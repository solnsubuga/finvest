import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { ClientsScreen } from "./index.tsx";
import { createHttpRecoveryApi, createStubRecoveryApi, notConnectedRecoveryApi, RecoveryError, type RecoveryApi } from "./recovery.ts";
import { createStubClientsApi } from "./stub.ts";
import { createStubSuppressionsApi } from "./suppressionsStub.ts";

const ME = { id: "stub-admin", name: "Solomon N." };
const NOW = Date.parse("2026-11-14T09:00:00Z");

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.search}</p>;
}

function renderScreen(recovery: RecoveryApi | null = createStubRecoveryApi(() => NOW, 0)) {
  render(
    <MemoryRouter initialEntries={["/clients"]}>
      <Routes>
        <Route
          path="/clients"
          element={
            <>
              <ClientsScreen
                api={createStubClientsApi(ME, () => NOW)}
                suppressions={createStubSuppressionsApi(ME, () => NOW)}
                canLift={false}
                recovery={recovery}
              />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return userEvent.setup();
}

describe("requests for a call back", () => {
  it("lists open requests oldest first and opens the client", async () => {
    const user = renderScreen();
    const list = await screen.findByRole("list", { name: "Requests for a call back" });
    const [first, second] = within(list).getAllByRole("article");
    expect(within(first!).getByText("+256 772 123 456")).toBeInTheDocument();
    expect(within(first!).getByText("Lost their phone or SIM card")).toBeInTheDocument();
    expect(within(first!).getByText("+256 701 555 013")).toBeInTheDocument();
    expect(within(second!).getByText("(the number on the account)")).toBeInTheDocument();

    await user.click(within(first!).getByRole("button", { name: "Open the client" }));
    expect(screen.getByTestId("where")).toHaveTextContent("?client=-NstubClient00000042");
  });

  it("closes a request with how the call ended", async () => {
    const user = renderScreen();
    const card = await screen.findByRole("article", { name: "Request for +256 701 987 654" });
    await user.click(within(card).getByRole("button", { name: "Close the request" }));
    expect(within(card).getByRole("group", { name: "How did the call end?" })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Helped another way" }));
    expect(await screen.findByText(/Closed as “Helped another way”/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Request for +256 701 987 654" })).not.toBeInTheDocument();
  });

  it("says so when someone else closed it first, and refreshes the list", async () => {
    const stub = createStubRecoveryApi(() => NOW, 0);
    const recovery: RecoveryApi = {
      list: () => stub.list(),
      close: async (id) => {
        await stub.close(id, "helped");
        throw new RecoveryError("not_found");
      },
    };
    const user = renderScreen(recovery);
    const card = await screen.findByRole("article", { name: "Request for +256 772 123 456" });
    await user.click(within(card).getByRole("button", { name: "Close the request" }));
    await user.click(within(card).getByRole("button", { name: "Number changed" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Someone else has already closed that request.");
  });

  it("shows when no one is waiting, and when Finvest cannot be reached", async () => {
    const empty = createStubRecoveryApi(() => NOW, 0);
    for (const item of await empty.list()) await empty.close(item.id, "helped");
    renderScreen(empty);
    expect(await screen.findByText("No one is waiting for a call.")).toBeInTheDocument();
  });

  it("offers to try again when the list cannot be loaded", async () => {
    renderScreen(notConnectedRecoveryApi);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This console is not connected to Finvest yet.");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("is not shown without the permission to change numbers", async () => {
    renderScreen(null);
    await screen.findByText(/Find a client to see their account/);
    expect(screen.queryByRole("heading", { name: "Locked out, waiting for a call" })).not.toBeInTheDocument();
  });
});

describe("the recovery API client", () => {
  it("reads the list and closes with the outcome", async () => {
    const fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const path = String(url);
      if (init?.method === "GET") {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: "R1",
                client_id: "C1",
                account_phone: "+256772123456",
                contact_phone: "+256701555013",
                reason: "new_number",
                created_at: "2026-11-14T08:00:00Z",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      expect(path).toContain("/staff/recovery-requests/R1/close");
      expect(JSON.parse(String(init?.body))).toEqual({ outcome: "number_changed" });
      return new Response(null, { status: 204 });
    });
    const api = createHttpRecoveryApi(createHttp("https://api.test", fetch));
    expect(await api.list()).toEqual([
      { id: "R1", clientId: "C1", accountPhone: "+256772123456", contactPhone: "+256701555013", reason: "new_number", createdAt: "2026-11-14T08:00:00Z" },
    ]);
    await api.close("R1", "number_changed");
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
