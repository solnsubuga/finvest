import type { APIRoute } from "astro";
import { indexable } from "../config.ts";

/** Search engines are let in only when the build says the site is ready (PUBLIC_INDEXABLE). */
export const GET: APIRoute = () =>
  new Response(indexable ? "User-agent: *\nAllow: /\n" : "User-agent: *\nDisallow: /\n", {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
