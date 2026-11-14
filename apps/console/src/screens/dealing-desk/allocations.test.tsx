import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { AllocationSection } from "./Allocations.tsx";
import { AllocationError, notConnectedAllocationsApi, type AllocationsApi } from "./allocations.ts";
import { createHttpAllocationsApi, toAllocationError, type AllocationReportJson } from "./allocationsHttp.ts";
import { createStubAllocationsApi, SAMPLE_INVESTMENT } from "./allocationsStub.ts";

function renderReport(api: AllocationsApi = createStubAllocationsApi()) {
  render(<AllocationSection api={api} />);
  return { user: userEvent.setup() };
}

const product = (code: string) => screen.findByRole("region", { name: code });

describe("allocation report", () => {
  it("shows each product's bills, what stands behind investments and who still waits", async () => {
    renderReport();
    const bills = await product("TREA-91");
    // All 7,180m held stands behind investments.
    expect(within(bills).getAllByText("UGX 7,180,000,000")).toHaveLength(2);
    expect(within(bills).getByText("82 investments, UGX 232,000,000")).toBeInTheDocument();
    const table = within(bills).getByRole("table", { name: "Bills held in TREA-91, oldest purchase first" });
    const first = within(table).getByRole("rowheader", { name: "CSD-2026-0412" }).closest("tr") as HTMLElement;
    expect(within(first).getByText("15 Oct 2026")).toBeInTheDocument();
    expect(within(first).getByText("1,204")).toBeInTheDocument();
    expect(screen.getByText(/Figures for 13 Nov 2026/)).toBeInTheDocument();
  });

  it("finds the bills behind one investment", async () => {
    const { user } = renderReport();
    await product("TREA-91");
    const lookup = screen.getByRole("form", { name: "Bills behind one investment" });
    await user.click(within(lookup).getByRole("button", { name: "Show bills" }));
    expect(within(lookup).getByText("Enter the investment's ID.")).toBeInTheDocument();
    await user.type(within(lookup).getByLabelText("Investment ID"), SAMPLE_INVESTMENT);
    await user.click(within(lookup).getByRole("button", { name: "Show bills" }));
    const list = await screen.findByRole("list", { name: `Bills behind ${SAMPLE_INVESTMENT}` });
    expect(within(list).getByText(/TREA-91, CSD-2026-0412, matures 14 Jan 2027/)).toBeInTheDocument();
    expect(screen.getByText("In all UGX 2,500,000, at cost")).toBeInTheDocument();
  });

  it("says so when no bill stands behind an investment", async () => {
    const { user } = renderReport();
    await product("TREA-91");
    await user.type(screen.getByLabelText("Investment ID"), "-Nother");
    await user.click(screen.getByRole("button", { name: "Show bills" }));
    expect(await screen.findByText(/No bill stands behind -Nother now/)).toBeInTheDocument();
  });

  it("says so before any bill is held", async () => {
    renderReport(createStubAllocationsApi("empty"));
    expect(await screen.findByText(/No bills are held and no investments are running in UGX yet/)).toBeInTheDocument();
  });

  it("offers to try again when the report cannot be loaded", async () => {
    const getReport = vi.fn(notConnectedAllocationsApi.getReport).mockRejectedValueOnce(new AllocationError("unavailable"));
    const { user } = renderReport({ ...createStubAllocationsApi(), getReport });
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest.");
    getReport.mockImplementation(createStubAllocationsApi().getReport);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await product("TREA-182")).toBeInTheDocument();
  });
});

describe("allocation report over the API", () => {
  it("reads the report and the bills behind an investment", async () => {
    const json: AllocationReportJson = {
      currency: "UGX",
      on: "2026-11-13",
      products: [
        {
          product_code: "TREA-91",
          lots: [
            {
              lot_id: "l1",
              settled_on: "2026-11-02",
              matures_on: "2027-02-01",
              custodian_reference: "CSD-1",
              face: { minor: 10, currency: "UGX" },
              cost: { minor: 9, currency: "UGX" },
              allocated: { minor: 6, currency: "UGX" },
              unallocated: { minor: 3, currency: "UGX" },
              investments: 2,
            },
          ],
          cost_held: { minor: 9, currency: "UGX" },
          allocated: { minor: 6, currency: "UGX" },
          unallocated: { minor: 3, currency: "UGX" },
          investments: 2,
          owed: { minor: 6, currency: "UGX" },
          covered: { minor: 6, currency: "UGX" },
          investments_waiting: 0,
          waiting: { minor: 0, currency: "UGX" },
        },
      ],
    };
    const request = vi.fn().mockResolvedValueOnce(json).mockResolvedValueOnce({ investment_id: "a/b", bills: [], total: null });
    const api = createHttpAllocationsApi({ request, download: vi.fn() } as unknown as Http);
    const report = await api.getReport("UGX");
    expect(request).toHaveBeenCalledWith("GET", "/staff/dealing/allocations?currency=UGX");
    expect(report.products[0]?.lots[0]).toMatchObject({ lotId: "l1", unallocated: { minor: 3, currency: "UGX" }, investments: 2 });
    expect(report.products[0]?.investmentsWaiting).toBe(0);
    expect(await api.billsBehind("a/b")).toEqual({ investmentId: "a/b", bills: [], total: null });
    expect(request).toHaveBeenLastCalledWith("GET", "/staff/dealing/allocations/investments/a%2Fb");
  });

  it("reads the backend's refusals as the screen's own", () => {
    expect(toAllocationError(new ApiError("forbidden", 403)).code).toBe("forbidden");
    expect(toAllocationError(new ApiError("validation_failed", 422)).code).toBe("invalid");
    expect(toAllocationError(new TypeError("x")).code).toBe("unavailable");
  });
});
