import type { APIRoute } from "astro";
import { legalDocuments } from "../data/legal.ts";

/**
 * Every page search engines should know about, for Google Search Console and Bing Webmaster
 * Tools. robots.txt names it once the site is indexable. The 404 page, the statement check's
 * answers and the PDFs are left out.
 */
const paths = [
  "/",
  "/products/",
  "/how-it-works/",
  "/safety/",
  "/about/",
  "/help/",
  "/check/",
  ...legalDocuments.map((document) => `/legal/${document.slug}/`),
];

export const GET: APIRoute = ({ site }) => {
  const base = site ?? new URL("https://finvestug.com");
  const urls = paths
    .map((path) => `  <url><loc>${new URL(path, base).href}</loc></url>`)
    .join("\n");
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`,
    { headers: { "Content-Type": "application/xml; charset=utf-8" } },
  );
};
