import type { APIRoute } from "astro";
import { indexable } from "../config.ts";
import { linkPreviewAgents } from "../data/share.ts";

/**
 * Search engines are let in only when the build says the site is ready (PUBLIC_INDEXABLE). The
 * apps that draw a preview of a shared link are always let in: some of them (X, LinkedIn, Slack)
 * obey robots.txt and would otherwise show a bare address. They do not list pages in search, and
 * every page still says noindex until launch.
 */
const previews = linkPreviewAgents.map((agent) => `User-agent: ${agent}\nAllow: /\n`).join("\n");

export const GET: APIRoute = () =>
  new Response(indexable ? "User-agent: *\nAllow: /\n" : `${previews}\nUser-agent: *\nDisallow: /\n`, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
