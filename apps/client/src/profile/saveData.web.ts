import { API_URL } from "@/api/config";

import { ProfileError, refusalFor } from "./types";

export const DATA_PATH = "/client/data-export";

/**
 * In a browser: fetch the copy with the session cookie and hand it to the
 * browser as a download under its own name. Nothing is kept by the page.
 */
export async function saveMyData(fileName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${DATA_PATH}`, {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ProfileError("unavailable");
  }
  if (!response.ok) {
    let code: string | undefined;
    try {
      code = ((await response.json()) as { error?: { code?: string } }).error?.code;
    } catch {
      code = undefined;
    }
    throw refusalFor(code, response.status);
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
