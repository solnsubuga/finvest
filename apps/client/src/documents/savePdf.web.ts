import { API_URL } from "@/api/config";

import { PDF, SavedType } from "./fileTypes";

/**
 * In a browser: fetch the PDF, or a statement's Excel copy, with the session
 * cookie and hand it to the browser as a download under its own name. Nothing is kept by the page.
 */
export async function savePdf(path: string, fileName: string, type: SavedType = PDF): Promise<void> {
  const response = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: { Accept: type.mimeType },
  });
  if (!response.ok || !(response.headers.get("content-type") ?? "").startsWith(type.mimeType)) {
    throw new Error(`download_failed_${response.status}`);
  }
  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    // Give the browser a moment to start the download before the blob goes.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}
