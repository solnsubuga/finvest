import { Platform } from "react-native";

/**
 * Where the Finvest API is. Set EXPO_PUBLIC_API_URL at build time to point a
 * build elsewhere, such as a local backend on http://localhost:8000. A build
 * without it talks to production.
 */
export const API_URL = (process.env.EXPO_PUBLIC_API_URL || "https://api.finvestug.com").replace(/\/+$/, "");

/**
 * On the web the session is an HttpOnly cookie the browser keeps, and the
 * page never sees it. On a phone the API hands over a token, kept in the
 * device's secure store. The API tells them apart by the browser's Origin
 * header, which a phone app does not send.
 */
export const USES_COOKIE = Platform.OS === "web";
