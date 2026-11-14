/**
 * Identity checks over the real API (`/staff/identity-checks`, docs/identity-checks.md).
 *
 * The check comes with photo IDs only. Links to see them last five minutes and asking for
 * them is an audit event, so they are asked for when a check is opened, never for the queue.
 */

import { ApiError, type Http } from "@/api/http.ts";
import {
  KycError,
  type AutomaticCheck,
  type CheckDetail,
  type CheckStatus,
  type Decision,
  type DocumentType,
  type KycApi,
  type PhoneNameStatus,
  type PhotoKind,
  type QueueItem,
} from "./api.ts";

interface QueueJson {
  id: string;
  name: string | null;
  document_type: DocumentType;
  status: CheckStatus;
  submitted_at: string | null;
  review_flags: string[];
}

interface CheckJson extends QueueJson {
  client_id: string;
  document_country: string;
  surname: string | null;
  given_names: string | null;
  document_number_masked: string | null;
  date_of_birth: string | null;
  document_expires_on: string | null;
  photos: { id: string; kind: PhotoKind; live?: boolean }[];
  automatic_checks: AutomaticCheck[];
  phone_name?: { status: PhoneNameStatus; registered_name: string | null } | null;
  retake_photos: PhotoKind[];
  client_message: string | null;
  account_number: string | null;
  decided_at: string | null;
  allowed: Decision[];
}

interface PhotoLinksJson {
  photos: { id: string; kind: PhotoKind; url: string }[];
  expires_at: string;
}

export function toQueueItem(json: QueueJson): QueueItem {
  return {
    id: json.id,
    name: json.name,
    documentType: json.document_type,
    status: json.status,
    // Only drafts have no time, and drafts are never in the queue.
    submittedAt: json.submitted_at ?? "",
    reviewFlags: json.review_flags,
  };
}

export function toCheck(json: CheckJson, links: PhotoLinksJson | null): CheckDetail {
  const urls = new Map((links?.photos ?? []).map((photo) => [photo.id, photo.url]));
  return {
    ...toQueueItem(json),
    documentCountry: json.document_country,
    surname: json.surname,
    givenNames: json.given_names,
    documentNumberMasked: json.document_number_masked,
    dateOfBirth: json.date_of_birth,
    documentExpiresOn: json.document_expires_on,
    photos: json.photos.map((photo) => ({
      kind: photo.kind,
      url: urls.get(photo.id) ?? null,
      live: photo.live === true,
    })),
    automaticChecks: json.automatic_checks.map((item) => ({ check: item.check, verdict: item.verdict, score: item.score })),
    phoneName: json.phone_name ? { status: json.phone_name.status, registeredName: json.phone_name.registered_name } : null,
    retakePhotos: json.retake_photos,
    clientMessage: json.client_message,
    accountNumber: json.account_number,
    allowed: json.allowed,
  };
}

export function toKycError(error: unknown): KycError {
  if (error instanceof KycError) return error;
  if (!(error instanceof ApiError)) return new KycError("unavailable");
  switch (error.code) {
    // A reason or message the backend refused, such as one too long. A definite answer.
    case "validation_failed":
      return new KycError("invalid");
    case "not_found":
      return new KycError("not_found");
    // A decision the check's state no longer allows, or a document another approved client holds.
    case "conflict":
      return new KycError("conflict");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new KycError("not_confirmed");
    case "forbidden":
      return new KycError("forbidden");
    default:
      return new KycError("unavailable");
  }
}

export function createHttpKycApi(http: Http, now: () => number = Date.now): KycApi {
  const path = (id: string, action = "") => `/staff/identity-checks/${encodeURIComponent(id)}${action}`;

  // Links already given for a check. Each request for links is an audit event, so one look at a
  // check, and the decision after it, ask once while the links are still good.
  const given = new Map<string, { photos: string; links: PhotoLinksJson }>();
  const photoIds = (json: CheckJson) => json.photos.map((photo) => photo.id).join(",");

  async function linksFor(json: CheckJson): Promise<PhotoLinksJson | null> {
    const known = given.get(json.id);
    // The same photos as last time, with a minute to spare so a link never runs out on screen.
    if (known && known.photos === photoIds(json) && Date.parse(known.links.expires_at) - now() > 60_000) return known.links;
    // Without links the photos show as their names, and the rest of the check still loads.
    const links = await http.request<PhotoLinksJson>("POST", path(json.id, "/photo-links")).catch(() => null);
    if (links) given.set(json.id, { photos: photoIds(json), links });
    return links;
  }

  async function withPhotos(json: CheckJson): Promise<CheckDetail> {
    if (json.photos.length === 0) return toCheck(json, null);
    return toCheck(json, await linksFor(json));
  }

  async function act(id: string, action: string, body: Record<string, unknown>): Promise<CheckDetail> {
    try {
      return await withPhotos(await http.request<CheckJson>("POST", path(id, action), body));
    } catch (error) {
      throw toKycError(error);
    }
  }

  return {
    async listQueue() {
      try {
        const json = await http.request<{ items: QueueJson[] }>("GET", "/staff/identity-checks");
        return json.items.map(toQueueItem);
      } catch (error) {
        throw toKycError(error);
      }
    },
    async getCheck(id) {
      try {
        return await withPhotos(await http.request<CheckJson>("GET", path(id)));
      } catch (error) {
        throw toKycError(error);
      }
    },
    async showDocumentNumber(id) {
      try {
        return (await http.request<{ document_number: string }>("POST", path(id, "/document-number"))).document_number;
      } catch (error) {
        throw toKycError(error);
      }
    },
    approve: (id, reason) => act(id, "/approve", { reason }),
    reject: (id, reason, clientMessage) => act(id, "/reject", { reason, client_message: clientMessage || null }),
    askForPhotos: (id, photos, clientMessage) => act(id, "/ask-for-photos", { photos, client_message: clientMessage }),
    escalate: (id, reason) => act(id, "/escalate", { reason }),
  };
}
