/**
 * Goals and group goals over the real API (`/staff/goals`, `/staff/goal-groups` in
 * backend/app/modules/goals/api.py, see docs/goals.md).
 *
 * The goals routes hold client and product IDs only: the goals module does not read the identity
 * or investments tables. The client's name and account number come from the shared directory
 * (`/staff/clients/cards`), and product names from the catalogue: `/staff/products` for staff who
 * may see products, else the public list of open products, else the product's code.
 */

import type {
  StaffGoalDetail,
  StaffGoalList,
  StaffGoalView,
  StaffGroupDetail,
  StaffGroupList,
  StaffGroupView,
  StaffMemberView,
  StaffSavingView,
} from "@finvest/api-client/staff";
import { ApiError, apiBaseUrl, createHttp, type Http } from "@/api/http.ts";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { GoalsError, notConnectedGoalsApi, type Goal, type GoalsApi, type Saving } from "./api.ts";
import { notConnectedGroupsApi, type Group, type GroupMember, type GroupsApi } from "./groupsApi.ts";

/** The most either list route gives at once. Neither has a next page yet. */
export const PAGE = 200;

/** A Kampala date, `YYYY-MM-DD`. */
function kampalaDay(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date(iso));
}

const day = (iso: string | null) => (iso === null ? null : kampalaDay(iso));

export function toGoalsError(error: unknown): GoalsError {
  if (error instanceof GoalsError) return error;
  if (!(error instanceof ApiError)) return new GoalsError("unavailable");
  switch (error.code) {
    case "forbidden":
      return new GoalsError("forbidden");
    case "not_found":
      return new GoalsError("not_found");
    default:
      return new GoalsError("unavailable");
  }
}

/** Product names by code, read once. A failed read is tried again next time. */
export function productNames(http: Http): () => Promise<ReadonlyMap<string, string>> {
  let known: Promise<ReadonlyMap<string, string>> | null = null;
  const toMap = (products: readonly { code: string; name: string }[]) => new Map(products.map((p) => [p.code, p.name]));
  return () => {
    known ??= http
      .request<{ code: string; name: string }[]>("GET", "/staff/products")
      .then(toMap)
      // Without `products.view`, the open products everyone may see still name most goals.
      .catch(() => http.request<{ products: { code: string; name: string }[] }>("GET", "/public/products").then((json) => toMap(json.products)))
      .catch(() => {
        known = null;
        return new Map<string, string>();
      });
    return known;
  };
}

export function toGoal(json: StaffGoalView, card: ClientCard | null, names: ReadonlyMap<string, string>): Goal {
  return {
    id: json.goal_id,
    clientId: json.client_id,
    clientName: card?.name ?? "Client details could not be read",
    accountNumber: card?.accountNumber ?? null,
    name: json.name,
    category: json.category,
    currency: json.currency,
    target: String(json.target_minor),
    targetDate: json.target_date,
    status: json.status,
    productName: names.get(json.product_code) ?? json.product_code,
    createdOn: kampalaDay(json.created_at),
    closedOn: day(json.closed_at),
  };
}

export function toSaving(json: StaffSavingView, names: ReadonlyMap<string, string>): Saving {
  return {
    investmentId: json.investment_id,
    productName: names.get(json.product_code) ?? json.product_code,
    status: json.status,
    amount: String(json.amount_minor),
    accruedNet: String(json.accrued_net_minor),
    startDate: json.start_date,
    maturityDate: json.maturity_date,
    rolledFrom: null,
  };
}

export function toGroup(json: StaffGroupView): Group {
  return {
    id: json.group_id,
    ownerClientId: json.owner_client_id,
    name: json.name,
    category: json.category,
    currency: json.currency,
    target: String(json.target_minor),
    targetDate: json.target_date,
    status: json.status,
    createdOn: kampalaDay(json.created_at),
    closedOn: day(json.closed_at),
  };
}

export function toMember(json: StaffMemberView): GroupMember {
  return {
    goalId: json.goal_id,
    clientId: json.client_id,
    displayName: json.display_name,
    joinedOn: kampalaDay(json.joined_at),
    leftOn: day(json.left_at),
    value: String(json.value_minor),
    valueOn: json.value_on,
  };
}

function listQuery(status: string | null): string {
  const query = new URLSearchParams({ limit: String(PAGE) });
  if (status) query.set("status", status);
  return query.toString();
}

export function createHttpGoalsApi(http: Http, directory: Directory, names = productNames(http)): GoalsApi {
  const withNames = async (goals: readonly StaffGoalView[]) => {
    const [products, cards] = await Promise.all([names(), Promise.all(goals.map((goal) => directory.client(goal.client_id)))]);
    return { products, cards };
  };

  return {
    async listGoals(status) {
      try {
        const { goals } = await http.request<StaffGoalList>("GET", `/staff/goals?${listQuery(status)}`);
        const { products, cards } = await withNames(goals);
        return goals.map((goal, index) => toGoal(goal, cards[index] ?? null, products));
      } catch (error) {
        throw toGoalsError(error);
      }
    },
    async getGoal(goalId) {
      try {
        const json = await http.request<StaffGoalDetail>("GET", `/staff/goals/${encodeURIComponent(goalId)}`);
        const { products, cards } = await withNames([json.goal]);
        return {
          goal: toGoal(json.goal, cards[0] ?? null, products),
          progress: {
            value: String(json.progress.value_minor),
            interest: String(json.progress.interest_minor),
            awaitingPayment: String(json.progress.awaiting_payment_minor),
            percent: json.progress.percent,
            standing: json.progress.standing,
          },
          savings: json.savings.map((saving) => toSaving(saving, products)),
        };
      } catch (error) {
        throw toGoalsError(error);
      }
    },
  };
}

export function createHttpGroupsApi(http: Http): GroupsApi {
  return {
    async listGroups(status) {
      try {
        const { groups } = await http.request<StaffGroupList>("GET", `/staff/goal-groups?${listQuery(status)}`);
        return groups.map(toGroup);
      } catch (error) {
        throw toGoalsError(error);
      }
    },
    async getGroup(groupId) {
      try {
        const json = await http.request<StaffGroupDetail>("GET", `/staff/goal-groups/${encodeURIComponent(groupId)}`);
        return {
          group: toGroup(json.group),
          progress: { value: String(json.progress.value_minor), percent: json.progress.percent, standing: json.progress.standing },
          members: json.members.map(toMember),
        };
      } catch (error) {
        throw toGoalsError(error);
      }
    },
  };
}

/** The real API when the build names one, otherwise goals that say they are not connected. */
export function connectedGoalsApis(): { goals: GoalsApi; groups: GroupsApi } {
  const base = apiBaseUrl();
  if (!base) return { goals: notConnectedGoalsApi, groups: notConnectedGroupsApi };
  const http = createHttp(base);
  return { goals: createHttpGoalsApi(http, createHttpDirectory(http)), groups: createHttpGroupsApi(http) };
}
