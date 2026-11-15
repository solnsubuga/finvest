import type { APIRoute } from "astro";
import { appleAssociation } from "../../lib/app-association.ts";

export const GET: APIRoute = () =>
  new Response(JSON.stringify(appleAssociation(import.meta.env.PUBLIC_APPLE_TEAM_ID)), {
    headers: { "Content-Type": "application/json" },
  });
