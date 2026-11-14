import { operations, type OperationName, type Operations } from "@finvest/api-client/client";
import type { z } from "zod";

import { API_URL, USES_COOKIE } from "./config";
import { readToken } from "./token";

/**
 * One typed call to the Finvest API, by its method and path as the API
 * publishes them, such as `api("POST /client/login/start", { body })`.
 *
 * - The answer is checked against the generated validator before a screen
 *   sees it. Anything that does not match is treated like an outage, never
 *   shown half-read.
 * - A request that got no answer fails with `offline` when the connection
 *   failed, and `timeout` when nothing came back in time. Neither proves the
 *   request never arrived (a connection can drop after it left), so both are
 *   `uncertain`, and a screen that moves money must not treat them as
 *   "nothing happened".
 * - Every refusal carries the API's error code, so screens never parse text.
 */

export type ApiErrorCode =
  | "offline"
  | "timeout"
  | "bad_response"
  // The codes the API sends, from backend/app/shared/errors.py.
  | "validation_failed"
  | "not_found"
  | "unauthorized"
  | "invalid_credentials"
  | "invalid_code"
  | "passkey_failed"
  | "second_step_missing"
  | "sign_in_again"
  | "app_locked"
  | "expired"
  | "phone_taken"
  | "email_taken"
  | "leaked_password"
  | "forbidden"
  | "conflict"
  | "idempotency_key_required"
  | "idempotency_key_reused"
  | "method_not_allowed"
  | "payload_too_large"
  | "rate_limited"
  | "unavailable"
  | "internal_error";

const KNOWN_CODES = new Set<string>([
  "validation_failed",
  "not_found",
  "unauthorized",
  "invalid_credentials",
  "invalid_code",
  "passkey_failed",
  "second_step_missing",
  "sign_in_again",
  "app_locked",
  "expired",
  "phone_taken",
  "email_taken",
  "leaked_password",
  "forbidden",
  "conflict",
  "idempotency_key_required",
  "idempotency_key_reused",
  "method_not_allowed",
  "payload_too_large",
  "rate_limited",
  "unavailable",
  "internal_error",
]);

/** A field the API refused, by its dotted path such as `target_date`, and why. */
export type FieldProblem = { field: string; code: string };

/** The client asked to close their account and staff have not decided, so nothing new starts. */
export function isAccountClosing(error: ApiError): boolean {
  return error.code === "conflict" && error.fields.some((problem) => problem.field === "account" && problem.code === "closing");
}

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number | null;
  /** The API's request ID, for support. Never shown with personal data. */
  readonly requestId: string | null;
  readonly fields: FieldProblem[];

  constructor(
    code: ApiErrorCode,
    { status = null, requestId = null, fields = [] }: { status?: number | null; requestId?: string | null; fields?: FieldProblem[] } = {},
  ) {
    super(code);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.fields = fields;
  }

  /** True when the request may have reached the API, so its outcome is unknown. */
  get uncertain(): boolean {
    return (
      this.code === "offline" ||
      this.code === "timeout" ||
      this.code === "internal_error" ||
      this.code === "bad_response"
    );
  }
}

type Op<K extends OperationName> = Operations[K];
type Empty = Record<string, never>;
type ParamsInput<K extends OperationName> =
  [keyof Op<K>["params"]["shape"]] extends [never] ? { params?: Empty } : { params: z.input<Op<K>["params"]> };
type QueryInput<K extends OperationName> =
  [keyof Op<K>["query"]["shape"]] extends [never] ? { query?: Empty } : { query?: z.input<Op<K>["query"]> };
type BodyInput<K extends OperationName> = Op<K>["body"] extends z.ZodType
  ? { body: z.input<Op<K>["body"]> }
  : { body?: never };
type KeyInput<K extends OperationName> = Op<K>["idempotent"] extends true
  ? { idempotencyKey: string }
  : { idempotencyKey?: never };

export type ApiInput<K extends OperationName> = ParamsInput<K> &
  QueryInput<K> &
  BodyInput<K> &
  KeyInput<K> & { timeoutMs?: number; signal?: AbortSignal };

export type ApiOutput<K extends OperationName> = Op<K>["response"] extends z.ZodType
  ? z.output<Op<K>["response"]>
  : null;

/** Long enough for a slow mobile network, short enough that a client is not left waiting. */
export const DEFAULT_TIMEOUT_MS = 20_000;

type Listener = (error: ApiError) => void;
const unauthorizedListeners = new Set<Listener>();

/** Called when the API says the session is gone, so the app can go back to log in. */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

const lockedListeners = new Set<Listener>();

/** Called when the API says the phone app is locked and wants its PIN first. */
export function onLocked(listener: Listener): () => void {
  lockedListeners.add(listener);
  return () => lockedListeners.delete(listener);
}

export type Fetch = typeof fetch;
let fetcher: Fetch = (input, init) => fetch(input, init);

/** Tests replace the network. */
export function setFetch(next: Fetch | null): void {
  fetcher = next ?? ((input, init) => fetch(input, init));
}

function buildPath(template: string, params: Record<string, unknown> | undefined): string {
  return template.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = params?.[name];
    if (value === undefined || value === null || value === "") {
      throw new Error(`Missing path parameter ${name}`);
    }
    return encodeURIComponent(String(value));
  });
}

function buildQuery(query: Record<string, unknown> | undefined): string {
  if (!query) return "";
  const parts: string[] = [];
  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    parts.push(`${encodeURIComponent(name)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

function formData(body: Record<string, unknown>): FormData {
  const form = new FormData();
  for (const [name, value] of Object.entries(body)) {
    if (value === undefined || value === null) continue;
    if (typeof value === "object") {
      // A Blob on the web, or { uri, name, type } that React Native's FormData reads from disk.
      form.append(name, value as Blob);
    } else {
      form.append(name, String(value));
    }
  }
  return form;
}

async function refusal(response: Response): Promise<ApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const envelope = payload as { request_id?: unknown; error?: { code?: unknown; fields?: unknown } } | null;
  const code = typeof envelope?.error?.code === "string" && KNOWN_CODES.has(envelope.error.code)
    ? (envelope.error.code as ApiErrorCode)
    : response.status >= 500
      ? "internal_error"
      : "unavailable";
  // The API names a field by its path, such as ["target_date"] or ["items", 0, "amount"].
  const fields = Array.isArray(envelope?.error?.fields)
    ? (envelope.error.fields as { field_path?: unknown; code?: unknown }[])
        .filter((f) => Array.isArray(f.field_path) && typeof f.code === "string")
        .map((f) => ({ field: (f.field_path as unknown[]).map(String).join("."), code: f.code as string }))
    : [];
  const requestId = typeof envelope?.request_id === "string" ? envelope.request_id : response.headers.get("x-request-id");
  return new ApiError(code, { status: response.status, requestId, fields });
}

export async function api<K extends OperationName>(name: K, input: ApiInput<K>): Promise<ApiOutput<K>> {
  const op = operations[name];
  const loose = input as {
    params?: Record<string, unknown>;
    query?: Record<string, unknown>;
    body?: Record<string, unknown>;
    idempotencyKey?: string;
    timeoutMs?: number;
    signal?: AbortSignal;
  };
  const url = `${API_URL}${buildPath(op.path, loose.params)}${buildQuery(loose.query)}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (op.bodyKind === "json") {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(loose.body ?? {});
  } else if (op.bodyKind === "multipart") {
    // The browser or React Native sets the multipart boundary itself.
    body = formData(loose.body ?? {});
  }
  if (op.idempotent) {
    if (!loose.idempotencyKey) throw new Error(`${name} needs an idempotency key`);
    headers["Idempotency-Key"] = loose.idempotencyKey;
  }
  if (!USES_COOKIE) {
    const token = await readToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, loose.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const forward = () => controller.abort();
  loose.signal?.addEventListener("abort", forward);

  let response: Response;
  try {
    response = await fetcher(url, {
      method: op.method,
      headers,
      body,
      signal: controller.signal,
      credentials: USES_COOKIE ? "include" : "omit",
    });
  } catch (error) {
    if (timedOut) throw new ApiError("timeout");
    if (loose.signal?.aborted) throw error;
    // fetch rejects when no answer came back: no network, DNS, TLS, a refused connection, or
    // one that dropped after the request left. Only the last may have reached the API, and the
    // app cannot tell them apart, so `offline` counts as uncertain.
    throw new ApiError("offline");
  } finally {
    clearTimeout(timer);
    loose.signal?.removeEventListener("abort", forward);
  }

  if (!response.ok) {
    const error = await refusal(response);
    if (error.code === "unauthorized") {
      for (const listener of unauthorizedListeners) listener(error);
    }
    if (error.code === "app_locked") {
      for (const listener of lockedListeners) listener(error);
    }
    throw error;
  }
  if (!op.response) {
    return null as ApiOutput<K>;
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError("bad_response", { status: response.status, requestId: response.headers.get("x-request-id") });
  }
  const parsed = (op.response as z.ZodType).safeParse(payload);
  if (!parsed.success) {
    throw new ApiError("bad_response", { status: response.status, requestId: response.headers.get("x-request-id") });
  }
  return parsed.data as ApiOutput<K>;
}
