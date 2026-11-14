/**
 * /legal/<slug>.pdf: each legal page as a PDF, written once at build time.
 */

import type { APIRoute, GetStaticPaths } from "astro";
import { legalDocuments, type LegalDocument } from "../../data/legal.ts";
import { legalPdf } from "../../lib/legal-pdf.ts";

export const getStaticPaths = (() =>
  legalDocuments.map((document) => ({
    params: { doc: document.slug },
    props: { document },
  }))) satisfies GetStaticPaths;

export const GET: APIRoute = async ({ props, site }) => {
  const { document } = props as { document: LegalDocument };
  const page = new URL(`/legal/${document.slug}`, site);
  const bytes = await legalPdf(document, `${page.host}${page.pathname}`);
  return new Response(new Blob([new Uint8Array(bytes)]), { headers: { "Content-Type": "application/pdf" } });
};
