/**
 * Automatic investing over the real API: `/staff/autoinvest` in
 * backend/app/modules/autoinvest/api.py (see docs/automatic-investing.md).
 *
 * The runs route gives each run's client as an ID, so clients are named through the shared
 * directory, which reads their cards in batches. It answers 200 runs a page; a busy week is
 * read page by page, up to a limit, so the screen never stops at an arbitrary run.
 */

import type { PlanCountsView, StaffRunList, StaffRunView } from "@finvest/api-client/staff";
import { ApiError, type Http } from "@/api/http.ts";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { AutoinvestError, type AutoinvestApi, type Run } from "./api.ts";
import { addDays } from "./labels.ts";

/** Pages of 200 read for one week, at most. Ten thousand runs is far more than one week holds. */
const MAX_PAGES = 50;

export function toRun(json: StaffRunView, card: ClientCard | null): Run {
  return {
    id: json.run_id,
    planId: json.plan_id,
    dueOn: json.due_on,
    status: json.status,
    attempt: json.attempt === 2 ? 2 : 1,
    retryOn: json.retry_on,
    code: json.code,
    // A client the directory cannot name is shown without a name, never as nothing.
    clientName: card?.name ?? "A client",
    accountNumber: card?.accountNumber ?? "No account number yet",
    productName: json.product_name,
    amount: String(json.amount_minor),
    frequency: json.frequency,
    day: json.day,
    network: json.network,
  };
}

export function toAutoinvestError(error: unknown): AutoinvestError {
  if (error instanceof AutoinvestError) return error;
  if (error instanceof ApiError && error.code === "forbidden") return new AutoinvestError("forbidden");
  return new AutoinvestError("unavailable");
}

export function createHttpAutoinvestApi(http: Http, directory: Directory): AutoinvestApi {
  async function runsBetween(from: string, to: string): Promise<StaffRunView[]> {
    const found: StaffRunView[] = [];
    let before: string | null = null;
    for (let page = 0; page < MAX_PAGES; page++) {
      const query = new URLSearchParams({ date_from: from, date_to: to, limit: "200" });
      if (before) query.set("before_id", before);
      const list: StaffRunList = await http.request<StaffRunList>("GET", `/staff/autoinvest/runs?${query.toString()}`);
      found.push(...list.runs);
      if (!list.next_before_id) break;
      before = list.next_before_id;
    }
    return found;
  }

  return {
    async getOverview(today) {
      const from = addDays(today, -6);
      try {
        const [plans, runs] = await Promise.all([
          http.request<PlanCountsView>("GET", "/staff/autoinvest/plans/counts"),
          runsBetween(from, today),
        ]);
        const cards = await Promise.all(runs.map((run) => directory.client(run.client_id).catch(() => null)));
        return {
          plans: { active: plans.active, paused: plans.paused },
          from,
          to: today,
          runs: runs.map((run, index) => toRun(run, cards[index] ?? null)),
        };
      } catch (error) {
        throw toAutoinvestError(error);
      }
    },
  };
}
