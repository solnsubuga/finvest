/**
 * The one way the console talks to the Finvest API.
 *
 * The session lives in an HttpOnly cookie that the API sets, so every request
 * is sent with credentials and nothing about the session is ever readable
 * here or kept in web storage. Errors arrive as the API's envelope
 * (`{ request_id, error: { code, message, fields } }`) and become an ApiError
 * with the same code, so a screen decides what to say from the code alone.
 *
 * This stands in for the generated client in packages/api-client until that
 * exists. The paths and shapes follow the backend's routes.
 */

import { askToConfirm, CONFIRM_IT_IS_YOU, type Confirmer } from "./confirmation.ts";

export interface FieldProblem {
  field_path: readonly (string | number)[];
  code: string;
}

export class ApiError extends Error {
  constructor(
    /** The API's error code, or `unavailable` when it could not be reached. */
    readonly code: string,
    readonly status: number,
    readonly fields: readonly FieldProblem[] = [],
    /** From Retry-After on a 429, in seconds. */
    readonly retryAfterSeconds: number | null = null,
    readonly requestId: string | null = null,
  ) {
    super(code);
    this.name = "ApiError";
  }

  /** The code of the first field problem, such as `too_short`, or null. */
  get fieldCode(): string | null {
    return this.fields[0]?.code ?? null;
  }
}

export type Method = "GET" | "POST" | "PUT" | "DELETE";

export interface Http {
  /** `body` is sent as JSON, or as multipart form data when it is a FormData. */
  request<T>(method: Method, path: string, body?: unknown, init?: RequestOptions): Promise<T>;
  /**
   * For a file such as a CSV export. An export the server records is a POST,
   * with its choices as a JSON body.
   */
  download(path: string, init?: DownloadOptions): Promise<Blob>;
}

export interface RequestOptions {
  idempotencyKey?: string;
}

export interface DownloadOptions {
  method?: "GET" | "POST";
  body?: unknown;
}

type Fetch = typeof fetch;

/**
 * `baseUrl` is the API's origin, such as https://api.finvestug.com, with no trailing slash.
 *
 * `confirm` is asked when the API answers `confirm_it_is_you`. If the person
 * confirms, the request is sent once more, with the same idempotency key: the
 * API refused the first one before doing anything.
 */
export function createHttp(baseUrl: string, fetchImpl: Fetch = fetch, confirm: Confirmer = askToConfirm): Http {
  const base = baseUrl.replace(/\/+$/, "");

  async function send(method: Method, path: string, body: unknown, options?: RequestOptions) {
    const headers: Record<string, string> = { Accept: "application/json" };
    // A form, such as a statement file, sets its own multipart boundary.
    const form = body instanceof FormData;
    if (body !== undefined && !form) headers["Content-Type"] = "application/json";
    if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
    let response: Response;
    try {
      response = await fetchImpl(`${base}${path}`, {
        method,
        headers,
        credentials: "include",
        cache: "no-store",
        body: body === undefined ? null : form ? body : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("unavailable", 0);
    }
    if (!response.ok) throw await toError(response);
    return response;
  }

  async function sendConfirmed(method: Method, path: string, body: unknown, options?: RequestOptions) {
    try {
      return await send(method, path, body, options);
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== CONFIRM_IT_IS_YOU) throw error;
      if (!(await confirm())) throw error;
      return send(method, path, body, options);
    }
  }

  return {
    async request<T>(method: Method, path: string, body?: unknown, options?: RequestOptions) {
      const response = await sendConfirmed(method, path, body, options);
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    },
    async download(path: string, init?: DownloadOptions) {
      const response = await sendConfirmed(init?.method ?? "GET", path, init?.body);
      return response.blob();
    },
  };
}

async function toError(response: Response): Promise<ApiError> {
  const retry = Number.parseInt(response.headers.get("Retry-After") ?? "", 10);
  const retryAfter = Number.isFinite(retry) && retry >= 0 ? retry : null;
  try {
    const envelope = (await response.json()) as {
      request_id?: string;
      error?: { code?: string; fields?: FieldProblem[] };
    };
    const code = envelope.error?.code;
    if (typeof code === "string") {
      return new ApiError(
        code,
        response.status,
        envelope.error?.fields ?? [],
        retryAfter,
        envelope.request_id ?? null,
      );
    }
  } catch {
    // Not the API's envelope: a proxy or the edge answered.
  }
  return new ApiError(response.status >= 500 ? "unavailable" : "internal_error", response.status, [], retryAfter);
}

/** Where each deployed console finds its API, when the build does not say otherwise. */
export const DEPLOYED_API: Record<string, string> = {
  production: "https://api.finvestug.com",
  staging: "https://api-staging.finvestug.com",
};

/** The API's address for this build: VITE_API_URL, else the deploy's own, else null. */
export function apiBaseUrl(): string | null {
  const value = import.meta.env.VITE_API_URL;
  if (value) return value;
  return DEPLOYED_API[import.meta.env.VITE_DEPLOY_ENV ?? ""] ?? null;
}
