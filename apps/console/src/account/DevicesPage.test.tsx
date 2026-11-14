import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { DevicesScreen } from "./DevicesPage.tsx";
import { createHttpDevicesApi, createStubDevicesApi } from "./devices.ts";

const NOW = Date.parse("2026-11-14T09:00:00Z");

describe("your devices", () => {
  it("lists every device, this browser first, and signs out one", async () => {
    const user = userEvent.setup();
    render(<DevicesScreen api={createStubDevicesApi("sample", () => NOW, 0)} />);
    const list = await screen.findByRole("list", { name: "Signed-in devices" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => within(item).getByText(/on (Windows|Mac)/).textContent)).toEqual(["Chrome on WindowsThis browser", "Safari on Mac", "Edge on Windows"]);
    expect(within(items[0]!).queryByRole("button")).not.toBeInTheDocument();
    await user.click(within(items[1]!).getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Safari on Mac is signed out.");
    expect(within(screen.getByRole("list", { name: "Signed-in devices" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("asks before signing out every other device", async () => {
    const user = userEvent.setup();
    render(<DevicesScreen api={createStubDevicesApi("sample", () => NOW, 0)} />);
    await user.click(await screen.findByRole("button", { name: "Sign out every other device" }));
    const confirm = screen.getByRole("region", { name: "Sign out every other device" });
    expect(confirm).toHaveTextContent("Sign out all 2 other devices?");
    await user.click(within(confirm).getByRole("button", { name: "Sign them out" }));
    expect(await screen.findByRole("status")).toHaveTextContent("2 other devices are signed out.");
    expect(screen.getByText("You are signed in on this browser only.")).toBeInTheDocument();
  });

  it("speaks of one other device as it", async () => {
    const user = userEvent.setup();
    render(<DevicesScreen api={createStubDevicesApi("sample", () => NOW, 0)} />);
    const list = await screen.findByRole("list", { name: "Signed-in devices" });
    await user.click(within(within(list).getAllByRole("listitem")[1]!).getByRole("button", { name: "Sign out" }));
    await screen.findByText("Safari on Mac is signed out.");
    await user.click(screen.getByRole("button", { name: "Sign out every other device" }));
    const confirm = screen.getByRole("region", { name: "Sign out every other device" });
    expect(confirm).toHaveTextContent("Sign out the other device? Anyone using it must sign in again");
    expect(within(confirm).getByRole("button", { name: "Sign it out" })).toBeInTheDocument();
  });

  it("says so when the list cannot be read", async () => {
    render(<DevicesScreen api={createStubDevicesApi("error", () => NOW, 0)} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest");
  });

  it("calls the backend's own-session routes", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url, init) =>
      Promise.resolve(
        init?.method === "DELETE"
          ? new Response(null, { status: 204 })
          : new Response(
              JSON.stringify(
                String(url).endsWith("sign-out-others")
                  ? { ended: 3 }
                  : {
                      items: [
                        {
                          id: "-Nsession0000000001",
                          device: "Chrome on Windows",
                          transport: "cookie",
                          second_step: "passkey",
                          signed_in_at: "2026-11-14T07:00:00Z",
                          last_seen_at: "2026-11-14T08:59:00Z",
                          current: true,
                        },
                      ],
                    },
              ),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
      ),
    );
    const api = createHttpDevicesApi(createHttp("https://api.test", fetch));
    expect(await api.list()).toEqual([
      { id: "-Nsession0000000001", device: "Chrome on Windows", secondStep: "passkey", signedInAt: "2026-11-14T07:00:00Z", lastSeenAt: "2026-11-14T08:59:00Z", current: true },
    ]);
    await api.signOut("-Nsession0000000002");
    expect(await api.signOutOthers()).toBe(3);
    expect(fetch.mock.calls.map(([url, init]) => `${init?.method} ${String(url)}`)).toEqual([
      "GET https://api.test/staff/sessions",
      "DELETE https://api.test/staff/sessions/-Nsession0000000002",
      "POST https://api.test/staff/sessions/sign-out-others",
    ]);
  });
});
