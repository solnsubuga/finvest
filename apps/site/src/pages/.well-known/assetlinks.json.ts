import type { APIRoute } from "astro";
import { androidAssetLinks } from "../../lib/app-association.ts";

export const GET: APIRoute = () =>
  new Response(JSON.stringify(androidAssetLinks(import.meta.env.PUBLIC_ANDROID_CERT_SHA256)), {
    headers: { "Content-Type": "application/json" },
  });
