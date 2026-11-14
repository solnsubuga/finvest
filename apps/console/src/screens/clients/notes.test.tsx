import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { NotesSection } from "./Notes.tsx";
import { createHttpNotesApi, createStubNotesApi, notConnectedNotesApi, type NotesApi } from "./notes.ts";

const ME = { name: "Opio Peter" };
const NOW = Date.parse("2026-11-14T09:00:00Z");
const CLIENT = "-NstubClient00000042";

function renderNotes(api: NotesApi) {
  render(<NotesSection api={api} clientId={CLIENT} />);
  return userEvent.setup();
}

describe("staff notes on a client", () => {
  it("shows the newest note first and keeps a new one under the writer's name", async () => {
    const user = renderNotes(createStubNotesApi(ME, true, "sample", () => NOW, 0));
    const list = await screen.findByRole("list", { name: "Notes, newest first" });
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("failed on Tuesday");
    await user.click(screen.getByRole("button", { name: "Save note" }));
    expect(screen.getByText("Write the note first.")).toBeInTheDocument();
    await user.type(screen.getByLabelText(/New note/), "Asked for a call back on Monday.");
    await user.click(screen.getByRole("button", { name: "Save note" }));
    expect(await within(list).findByText("Asked for a call back on Monday.")).toBeInTheDocument();
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("Opio Peter");
    expect(screen.getByLabelText(/New note/)).toHaveValue("");
  });

  it("lets a role that cannot write read the notes, and says when there are none", async () => {
    renderNotes(createStubNotesApi(ME, false, "empty", () => NOW, 0));
    expect(await screen.findByText("No notes on this client yet.")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Write a note" })).not.toBeInTheDocument();
  });

  it("shows when notes are not connected", async () => {
    renderNotes(notConnectedNotesApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected");
  });

  it("reads and writes on the backend's paths", async () => {
    const note = { id: "-Nnote", client_id: CLIENT, body: "Hi", written_by: "-Ns", written_by_name: "Grace Auma", written_at: "2026-11-14T07:00:00Z" };
    const fetch = vi.fn<typeof globalThis.fetch>((_url, init) =>
      Promise.resolve(
        new Response(JSON.stringify(init?.method === "POST" ? note : { notes: [note], can_write: true }), {
          status: init?.method === "POST" ? 201 : 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const api = createHttpNotesApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));
    expect(await api.notesFor(CLIENT)).toEqual({
      notes: [{ id: "-Nnote", body: "Hi", writtenByName: "Grace Auma", writtenAt: "2026-11-14T07:00:00Z" }],
      canWrite: true,
    });
    await api.write(CLIENT, "Hi");
    expect(fetch.mock.calls.map(([url, init]) => [String(url), init?.method])).toEqual([
      [`https://api.test/staff/clients/${CLIENT}/notes`, "GET"],
      [`https://api.test/staff/clients/${CLIENT}/notes`, "POST"],
    ]);
  });
});
