import { ApiError, type Http } from "@/api/http.ts";
import { MovementsError, type Amount, type Movement, type MovementsApi } from "./api.ts";

interface MovementJson {
  kind: Movement["kind"];
  id: string;
  client_id: string;
  client_name: string | null;
  channel: string;
  amount: Amount;
  status: string;
  failure_code: string | null;
  created_at: string;
  ended_at: string | null;
}

export function createHttpMovementsApi(http: Http): MovementsApi {
  return {
    async list(show, after) {
      const params = new URLSearchParams();
      if (show !== "all") params.set("show", show);
      if (after) params.set("after", after);
      try {
        const found = await http.request<{ movements: MovementJson[]; next_after: string | null }>(
          "GET",
          `/staff/money-movements${params.size ? `?${params.toString()}` : ""}`,
        );
        return {
          movements: found.movements.map((row) => ({
            kind: row.kind,
            id: row.id,
            clientId: row.client_id,
            clientName: row.client_name,
            channel: row.channel,
            amount: row.amount,
            status: row.status,
            failureCode: row.failure_code,
            createdAt: row.created_at,
            endedAt: row.ended_at,
          })),
          nextAfter: found.next_after,
        };
      } catch (error) {
        throw new MovementsError(error instanceof ApiError && error.code === "forbidden" ? "forbidden" : "unavailable");
      }
    },
  };
}
