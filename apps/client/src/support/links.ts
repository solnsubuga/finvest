import { Href } from "expo-router";

import { Notification } from "./types";

/**
 * The screen a notification opens. A notification carries a kind and an ID,
 * never an address, so nothing sent to a client can lead outside the app.
 * A kind whose screen is not built yet opens nothing.
 */
export function hrefFor(link: Notification["link"]): Href | null {
  if (!link) {
    return null;
  }
  switch (link.kind) {
    case "deposit":
      return { pathname: "/pay/deposit/[depositId]", params: { depositId: link.id } };
    case "conversation":
      return "/help";
    case "complaint":
      return { pathname: "/support/complaints/[complaintId]", params: { complaintId: link.id } };
    case "security":
      return "/security";
    case "statement":
      return "/documents";
    case "investment":
      return "/portfolio";
    case "identity_check":
      return "/id-check";
    default:
      return null;
  }
}
