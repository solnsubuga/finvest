/**
 * Addresses the site links to. All public, set at build time.
 *
 * The site links to the client app and reads rates from the API. It never links to the back
 * office, and tests/built-site.test.ts fails the build output if it does.
 */

function origin(value: string | undefined, fallback: string): string {
  return (value && value.trim() !== "" ? value.trim() : fallback).replace(/\/+$/, "");
}

export const appUrl = origin(import.meta.env.PUBLIC_APP_URL, "https://app.finvestug.com");
export const apiUrl = origin(import.meta.env.PUBLIC_API_URL, "https://api.finvestug.com");

export const loginUrl = `${appUrl}/login`;
export const signupUrl = `${appUrl}/signup`;

/** Where the browser reads today's rates. See docs/decisions.md. */
export const productsEndpoint = `${apiUrl}/public/products`;
/** Where the calculator asks for the exact figure. */
export const quoteEndpoint = `${apiUrl}/public/quote`;

/** Search engines are kept out until the owner says launch. */
export const indexable = import.meta.env.PUBLIC_INDEXABLE === "true";

/** Kept with the other facts about Finvest, so scripts run outside the build can read it too. */
export { supportEmail } from "./data/company.ts";

/** Where the statement check page asks whether a code is on a statement Finvest issued. */
export const statementCheckEndpoint = `${apiUrl}/public/statement-checks`;

/** Where the browser reads the bonds clients can buy today. */
export const bondsEndpoint = `${apiUrl}/public/bonds`;
