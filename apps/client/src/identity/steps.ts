import { DocumentType, IdentityCheck, PhotoKind, requiredPhotos } from "./types";

/**
 * The order of the ID check, as on the design canvas: choose the document,
 * the front, the back (not for a passport), confirm the details, then the selfie.
 */
export type Step = "document" | PhotoKind | "details";

export function stepsFor(documentType: DocumentType | null): Step[] {
  const photos = requiredPhotos(documentType ?? "national_id");
  return ["document", ...photos.filter((kind) => kind !== "selfie"), "details", "selfie"];
}

/** "2 of 5": where a step sits, for the progress bar. */
export function stepNumber(step: Step, documentType: DocumentType | null): { step: number; of: number } {
  const steps = stepsFor(documentType);
  return { step: steps.indexOf(step) + 1, of: steps.length };
}

export function hrefFor(step: Step): string {
  if (step === "document") return "/id-check/document";
  if (step === "details") return "/id-check/details";
  return `/id-check/photo/${step}`;
}

/** The first step of a draft still to do, or null when it is ready to hand in. */
export function nextStep(check: IdentityCheck | null): Step | null {
  if (!check) return "document";
  if (check.status !== "draft") return null;
  for (const step of stepsFor(check.documentType)) {
    if (step === "document") continue;
    if (check.missing.includes(step)) return step;
  }
  return null;
}

/** The step after this one in a draft, skipping what is already done. Null when all are done. */
export function stepAfter(step: Step, check: IdentityCheck): Step | null {
  const steps = stepsFor(check.documentType);
  const later = steps.slice(steps.indexOf(step) + 1);
  return later.find((candidate) => check.missing.includes(candidate)) ?? nextStep(check);
}
