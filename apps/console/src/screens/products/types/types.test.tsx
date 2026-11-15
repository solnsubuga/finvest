import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/http.ts";
import type { CatalogueApi, NewProductDraft } from "../api.ts";
import { createStubBondsApi } from "../bonds/stub.ts";
import { ProductsScreen } from "../index.tsx";
import { createStubCatalogueApi } from "../stub.ts";
import type { ProductTypesApi } from "./api.ts";
import { toProductTypeChange, toProductTypesError } from "./http.ts";
import { createStubProductTypesApi } from "./stub.ts";

function renderTypes(
  typesApi: ProductTypesApi = createStubProductTypesApi({ me: "me" }),
  { at = "/products", canChange = true, api = createStubCatalogueApi({ me: "me", today: "2026-11-15" }) }: { at?: string; canChange?: boolean; api?: CatalogueApi } = {},
) {
  render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path="/products" element={<ProductsScreen api={api} bondsApi={createStubBondsApi({ me: "me" })} typesApi={typesApi} me="me" canChange={canChange} />} />
      </Routes>
    </MemoryRouter>,
  );
  return userEvent.setup();
}

function row(type: string): string[] {
  const table = screen.getByRole("table", { name: "Product types" });
  const header = within(table).getByRole("rowheader", { name: new RegExp(`^${type}`) });
  return within(header.closest("tr") as HTMLElement)
    .getAllByRole("cell")
    .map((cell) => cell.textContent ?? "");
}

/** The catalogue stub, remembering the drafts it was sent. */
function recording(): { api: CatalogueApi; drafts: NewProductDraft[] } {
  const api = createStubCatalogueApi({ me: "me", today: "2026-11-15", sample: "empty" });
  const drafts: NewProductDraft[] = [];
  return {
    drafts,
    api: {
      ...api,
      proposeNewProduct: (draft) => {
        drafts.push(draft);
        return api.proposeNewProduct(draft);
      },
    },
  };
}

describe("product types", () => {
  it("lists each type with how it earns, its usual tax, its products and status", async () => {
    renderTypes();
    await screen.findByRole("table", { name: "Product types" });
    expect(row("Treasury bill")).toEqual(["Term and rate, paid at maturity", "15%", "3", "In use", "Change Treasury bill"]);
    expect(row("Treasury bond")).toEqual(["Bank of Uganda bond with coupons", "20%", "3", "In use", "Change Treasury bond"]);
    expect(row("Holiday bill")).toEqual(["Term and rate, paid at maturity", "15%", "1", "Retired", "Change Holiday bill"]);
    expect(screen.getByRole("link", { name: "New product type" })).toHaveAttribute("href", "/products?new-type");
  });

  it("shows types but no changes to staff who cannot change products", async () => {
    renderTypes(undefined, { canChange: false });
    await screen.findByRole("table", { name: "Product types" });
    expect(row("Savings note")).toEqual(["Term and rate, paid at maturity or monthly", "15%", "0", "In use"]);
    expect(screen.queryByRole("link", { name: "New product type" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve as second person" })).toBeNull();
  });

  it("lists the types in use on New product, and sends the type chosen", async () => {
    const { api, drafts } = recording();
    const user = renderTypes(undefined, { at: "/products?new", api });
    const type = await screen.findByLabelText("Type");
    const options = within(type).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["Treasury bill", "Fixed income", "Treasury bond", "Savings note"]);
    expect(screen.getByRole("link", { name: "Define a new product type" })).toHaveAttribute("href", "/products?new-type");
    await user.selectOptions(type, "Savings note");
    expect(screen.getByText("Money set aside for a goal, earning monthly interest with no fixed end date.")).toBeInTheDocument();
    expect(screen.getByLabelText("Tax on interest, %")).toHaveValue("15");
    await user.type(screen.getByLabelText("Code"), "SAVE-1");
    await user.type(screen.getByLabelText("Name"), "Goal Saver");
    await user.click(screen.getByLabelText("Open term, until the client withdraws"));
    await user.type(screen.getByLabelText("Minimum, UGX"), "50,000");
    await user.type(screen.getByLabelText("Yearly rate before tax, %"), "11");
    await user.type(screen.getByLabelText("Reason, for the audit trail"), "Savers asked for it");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    await screen.findByRole("region", { name: "Goal Saver" });
    expect(drafts[0]).toMatchObject({ typeCode: "SAVINGS-NOTE", kind: "fixed_income", termDays: null });
  });

  it("opens an older link on the first type built on that model", async () => {
    renderTypes(undefined, { at: "/products?new&type=fixed_income" });
    expect(await screen.findByLabelText("Type")).toHaveValue("FIXED-INCOME");
  });

  it("falls back to the built-in types when the types cannot be loaded", async () => {
    const { api, drafts } = recording();
    const user = renderTypes(createStubProductTypesApi({ me: "me", sample: "offline" }), { at: "/products?new", api });
    const type = await screen.findByLabelText("Type");
    expect(within(type).getAllByRole("option").map((option) => option.textContent)).toEqual(["Treasury bill", "Fixed income", "Treasury bond"]);
    await user.type(screen.getByLabelText("Code"), "TREA-364");
    await user.type(screen.getByLabelText("Name"), "364 Day Treasury Bill");
    await user.type(screen.getByLabelText("Term, days"), "364");
    await user.type(screen.getByLabelText("Minimum, UGX"), "100000");
    await user.type(screen.getByLabelText("Yearly rate before tax, %"), "15");
    await user.type(screen.getByLabelText("Reason, for the audit trail"), "A longer term");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    await screen.findByRole("region", { name: "364 Day Treasury Bill" });
    expect(drafts[0]?.typeCode).toBeUndefined();
    expect(await screen.findByRole("heading", { name: "Product types could not be loaded" })).toBeInTheDocument();
  });

  it("defines a new type on a return model and sends it for a second person", async () => {
    const typesApi = createStubProductTypesApi({ me: "me", sample: "empty" });
    const user = renderTypes(typesApi, { at: "/products?new-type" });
    await screen.findByRole("heading", { name: "New product type", level: 1 });
    await user.click(screen.getByRole("radio", { name: /Term and rate, paid at maturity or monthly/ }));
    expect(screen.getByText("term and rate, paid at maturity or monthly")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(screen.getByText("Use capital letters, digits and hyphens, such as COMPANY-NOTE.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Code"), "company-note");
    expect(screen.getByLabelText("Code")).toHaveValue("COMPANY-NOTE");
    await user.type(screen.getByLabelText("Name"), "Company note");
    await user.type(screen.getByLabelText("Description"), "A fixed term note for companies.");
    await user.clear(screen.getByLabelText("Usual tax, %"));
    await user.type(screen.getByLabelText("Usual tax, %"), "10");
    await user.type(screen.getByLabelText("Reason, for the audit trail"), "Companies asked");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    const card = await screen.findByRole("region", { name: "Company note" });
    expect(within(card).getByText("New product type · waiting for a second person")).toBeInTheDocument();
    expect(within(card).getByText("10%")).toBeInTheDocument();
    const [change] = await typesApi.listChanges("proposed");
    expect(change).toMatchObject({ code: "COMPANY-NOTE", model: "fixed_income", terms: { name: "Company note", defaultTaxBps: 1000 } });
  });

  it("refuses a name another type already has", async () => {
    const user = renderTypes(undefined, { at: "/products?new-type" });
    await screen.findByRole("heading", { name: "New product type", level: 1 });
    await user.type(screen.getByLabelText("Code"), "BILL-2");
    await user.type(screen.getByLabelText("Name"), "Treasury bill");
    await user.type(screen.getByLabelText("Description"), "Again");
    await user.type(screen.getByLabelText("Reason, for the audit trail"), "Test");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A type with this name already exists");
  });

  it("approves a colleague's new type, which then appears on New product", async () => {
    const user = renderTypes();
    const card = await screen.findByRole("region", { name: "Company note" });
    expect(within(card).getByText("New product type · waiting for you")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    await screen.findByRole("rowheader", { name: /^Company note/ });
    expect(row("Company note")).toEqual(["Term and rate, paid at maturity or monthly", "15%", "0", "In use", "Change Company note"]);
    expect(screen.queryByRole("region", { name: "Company note" })).toBeNull();
  });

  it("sends a colleague's type back with a note", async () => {
    const typesApi = createStubProductTypesApi({ me: "me" });
    const user = renderTypes(typesApi);
    const card = await screen.findByRole("region", { name: "Company note" });
    await user.click(within(card).getByRole("button", { name: "Send back with a note" }));
    await user.type(within(card).getByLabelText("Why it is going back"), "Say which companies");
    await user.click(within(card).getByRole("button", { name: "Send back" }));
    await screen.findByRole("table", { name: "Product types" });
    expect(screen.queryByRole("region", { name: "Company note" })).toBeNull();
    expect((await typesApi.listChanges("sent_back"))[0]?.decisionNote).toBe("Say which companies");
  });

  it("proposes a new name and tax for a type, keeping how it earns", async () => {
    const typesApi = createStubProductTypesApi({ me: "me" });
    const user = renderTypes(typesApi, { at: "/products?edit-type=SAVINGS-NOTE" });
    await screen.findByRole("heading", { name: "Change product type", level: 1 });
    expect(screen.getByText("Term and rate, paid at maturity or monthly")).toBeInTheDocument();
    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Goal saver");
    await user.clear(screen.getByLabelText("Usual tax, %"));
    await user.type(screen.getByLabelText("Usual tax, %"), "12.5");
    await user.type(screen.getAllByLabelText("Reason, for the audit trail")[0] as HTMLElement, "Clearer name");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    const card = await screen.findByRole("region", { name: "Savings note" });
    expect(within(card).getByText("Savings note to Goal saver")).toBeInTheDocument();
    expect(within(card).getByText("15% to 12.5%")).toBeInTheDocument();
    expect(screen.getByText(/Nothing else can be proposed until it is decided/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send retiring for approval" })).toBeDisabled();
  });

  it("sends retiring a type for a second person", async () => {
    const typesApi = createStubProductTypesApi({ me: "me", sample: "empty" });
    const user = renderTypes(typesApi, { at: "/products?edit-type=FIXED-INCOME" });
    await screen.findByRole("heading", { name: "Retire this type" });
    await user.click(screen.getByRole("button", { name: "Send retiring for approval" }));
    expect(screen.getByText("Say why. The reason goes in the audit trail.")).toBeInTheDocument();
    await user.type(screen.getAllByLabelText("Reason, for the audit trail")[1] as HTMLElement, "Replaced by Savings note");
    await user.click(screen.getByRole("button", { name: "Send retiring for approval" }));
    const card = await screen.findByRole("region", { name: "Fixed income" });
    expect(within(card).getByText("Retire type · waiting for a second person")).toBeInTheDocument();
    expect(within(card).getByText("In use to Retired")).toBeInTheDocument();
    expect((await typesApi.listChanges("proposed"))[0]).toMatchObject({ action: "retire", code: "FIXED-INCOME" });
  });

  it("withdraws one's own change", async () => {
    const typesApi = createStubProductTypesApi({ me: "me", sample: "empty" });
    await typesApi.proposeStatus("TREASURY-BILL", "retired", "Test");
    const user = renderTypes(typesApi);
    const card = await screen.findByRole("region", { name: "Treasury bill" });
    await user.click(within(card).getByRole("button", { name: "Withdraw this change" }));
    await screen.findByRole("table", { name: "Product types" });
    expect(screen.queryByRole("region", { name: "Treasury bill" })).toBeNull();
  });

  it("shows an error with a way to try again when types cannot be loaded", async () => {
    renderTypes(createStubProductTypesApi({ me: "me", sample: "offline" }), { at: "/products?new-type" });
    expect(await screen.findByRole("heading", { name: "Product types could not be loaded" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("product types over HTTP", () => {
  it("maps the backend's refusals", () => {
    expect(toProductTypesError(new ApiError("validation_failed", 422, [{ field_path: ["name"], code: "name_taken" }])).code).toBe("name_taken");
    expect(toProductTypesError(new ApiError("validation_failed", 422, [{ field_path: ["code"], code: "something_new" }])).code).toBe("invalid");
    expect(toProductTypesError(new ApiError("conflict", 409)).code).toBe("conflict");
    expect(toProductTypesError(new ApiError("confirm_it_is_you", 403)).code).toBe("not_confirmed");
    expect(toProductTypesError(new Error("network")).code).toBe("unavailable");
  });

  it("reads a retirement as a change with no terms", () => {
    const change = toProductTypeChange({
      id: "-TypeChange000000001",
      action: "retire",
      code: "HOLIDAY-BILL",
      model: "treasury_bill",
      name: null,
      description: null,
      default_tax_bps: null,
      reason: "Season over",
      status: "proposed",
      proposed_by: "-Staff00000000000001",
      proposed_at: "2026-11-15T08:00:00Z",
      decided_by: null,
      decided_at: null,
      decision_note: null,
    });
    expect(change.terms).toBeNull();
    expect(change.action).toBe("retire");
  });
});
