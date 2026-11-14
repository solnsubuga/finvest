import { Platform } from "react-native";
import type { ClientCheck, Upload } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";

import { DetailField, IdentityCheck, IdentityClient, IdentityError, PhotoFile, PhotoKind } from "./types";

/**
 * The ID check screens' contract, answered by the real API (`/client/identity-check`,
 * backend/app/modules/kyc). The API never sends provider answers, flags or who decided,
 * so neither does this.
 */

const FIELD_NAMES: Record<string, DetailField> = {
  surname: "surname",
  given_names: "givenNames",
  document_number: "documentNumber",
  date_of_birth: "dateOfBirth",
  document_expires_on: "expiresOn",
  tin: "tin",
};

export function toCheck(view: ClientCheck): IdentityCheck {
  const details = view.details;
  return {
    id: view.id,
    status: view.status,
    documentType: view.document_type,
    documentCountry: view.document_country,
    details: details
      ? {
          surname: details.surname,
          givenNames: details.given_names,
          documentNumber: details.document_number,
          dateOfBirth: details.date_of_birth,
          expiresOn: details.document_expires_on,
          tin: details.tin ?? null,
        }
      : null,
    detailsConfirmed: view.details_confirmed,
    photos: view.photos.map((photo) => photo.kind),
    missing: view.missing,
    retakePhotos: view.retake_photos,
    clientMessage: view.client_message,
    submittedAt: view.submitted_at,
    accountNumber: view.account_number,
  };
}

type Action = "read" | "document" | "photo" | "details" | "submit";

export function toIdentityError(error: unknown, action: Action): IdentityError {
  if (error instanceof IdentityError) return error;
  if (!(error instanceof ApiError)) return new IdentityError("unavailable");
  if (error.code === "offline" || error.code === "timeout") return new IdentityError("offline");
  switch (error.code) {
    case "not_found":
      return new IdentityError("not_found");
    case "conflict":
      // During a retake, a photo staff did not ask for is refused the same way as a decided check.
      return new IdentityError(action === "photo" ? "photo_not_needed" : "cannot_change");
    case "payload_too_large":
      return new IdentityError("photo_not_accepted");
    case "validation_failed": {
      if (action === "photo") {
        const notPart = error.fields.some((problem) => problem.code === "photo_not_part_of_document");
        return new IdentityError(notPart ? "photo_not_needed" : "photo_not_accepted");
      }
      if (action === "submit") {
        return new IdentityError("incomplete", { missing: error.fields.map((problem) => problem.field) });
      }
      if (action === "details") {
        const fields = error.fields.flatMap((problem) => {
          const field = FIELD_NAMES[problem.field.split(".").pop() ?? ""];
          return field ? [{ field, code: problem.code }] : [];
        });
        return new IdentityError("invalid_details", { fields });
      }
      return new IdentityError("unavailable");
    }
    default:
      return new IdentityError("unavailable");
  }
}

/**
 * On the web the picker hands back a blob: or data: address, which a form cannot read, so the
 * file is fetched into a File first. On a phone, FormData reads `{ uri, name, type }` from disk.
 */
async function uploadable(file: PhotoFile): Promise<Upload> {
  if (Platform.OS === "web") {
    const blob = await (await fetch(file.uri)).blob();
    return new File([blob], file.name, { type: file.mimeType });
  }
  return { uri: file.uri, name: file.name, type: file.mimeType };
}

export function createApiIdentityClient(): IdentityClient {
  return {
    async getCheck() {
      try {
        const { check } = await api("GET /client/identity-check", {});
        return check ? toCheck(check) : null;
      } catch (error) {
        throw toIdentityError(error, "read");
      }
    },

    async chooseDocument(documentType, country) {
      try {
        return toCheck(
          await api("PUT /client/identity-check/document", {
            body: { document_type: documentType, document_country: country },
          }),
        );
      } catch (error) {
        throw toIdentityError(error, "document");
      }
    },

    async sendPhoto(kind: PhotoKind, file) {
      try {
        return toCheck(
          await api("PUT /client/identity-check/photos/{kind}", {
            params: { kind },
            body: { photo: await uploadable(file) },
            // A 10 MB photo on a slow mobile network takes longer than a JSON call.
            timeoutMs: 90_000,
          }),
        );
      } catch (error) {
        throw toIdentityError(error, "photo");
      }
    },

    async confirmDetails(details) {
      try {
        return toCheck(
          await api("PUT /client/identity-check/details", {
            body: {
              surname: details.surname,
              given_names: details.givenNames,
              document_number: details.documentNumber,
              date_of_birth: details.dateOfBirth,
              document_expires_on: details.expiresOn,
              tin: details.tin,
            },
          }),
        );
      } catch (error) {
        throw toIdentityError(error, "details");
      }
    },

    async submit() {
      try {
        return toCheck(await api("POST /client/identity-check/submit", {}));
      } catch (error) {
        throw toIdentityError(error, "submit");
      }
    },
  };
}
