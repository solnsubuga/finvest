import { Platform } from "react-native";
import Constants from "expo-constants";

/**
 * Where the Finvest API is. Set EXPO_PUBLIC_API_URL at build time to point a
 * build elsewhere. Without it, a development build talks to a local backend
 * (`make dev`), so test sign-ups never reach production, and only a
 * production build talks to production.
 */
const FALLBACK = __DEV__ ? "http://localhost:8000" : "https://api.finvestug.com";
export const API_URL = (process.env.EXPO_PUBLIC_API_URL || FALLBACK).replace(/\/+$/, "");

/**
 * On the web the session is an HttpOnly cookie the browser keeps, and the
 * page never sees it. On a phone the API hands over a token, kept in the
 * device's secure store. The API tells them apart by the browser's Origin
 * header, which a phone app does not send.
 */
export const USES_COOKIE = Platform.OS === "web";

/**
 * How the phone app names itself to the API, so Devices in Sign in and security says "Finvest
 * app on Android" rather than "Unknown device" (the API keeps only known words from it). A
 * browser sends its own, so the web build sends none.
 */
export const APP_USER_AGENT: string | null =
  Platform.OS === "web"
    ? null
    : `Finvest/${Constants.expoConfig?.version ?? "0"} (${Platform.OS === "ios" ? "iPhone" : "Android"})`;
