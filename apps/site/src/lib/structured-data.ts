/**
 * Structured data (schema.org, as JSON-LD) that search engines read to understand who Finvest
 * is and what a page answers. It states only what the pages already say, and leaves out every
 * fact Finvest has not supplied (src/data/company.ts), as the pages do.
 */

import { company, supportEmail } from "../data/company.ts";

export type StructuredData = Record<string, unknown>;

/** Finvest as a financial service for people in Uganda. */
export function organisation(site: URL): StructuredData {
  const [locality] = company.city.split(",");
  return {
    "@context": "https://schema.org",
    "@type": "FinancialService",
    "@id": new URL("/#organisation", site).href,
    name: "Finvest",
    ...(company.registeredName ? { legalName: company.registeredName } : {}),
    url: new URL("/", site).href,
    logo: new URL("/share/logo.png", site).href,
    image: new URL("/share/home.png", site).href,
    description:
      "Finvest lets people in Uganda invest in Government of Uganda Treasury bills and bonds from their phone, paying with MTN Mobile Money, Airtel Money or bank transfer.",
    email: supportEmail,
    ...(company.phone ? { telephone: company.phone } : {}),
    address: {
      "@type": "PostalAddress",
      ...(company.officeAddress ? { streetAddress: company.officeAddress } : {}),
      addressLocality: locality?.trim(),
      addressCountry: "UG",
    },
    areaServed: { "@type": "Country", name: "Uganda" },
    currenciesAccepted: "UGX",
    paymentAccepted: "MTN Mobile Money, Airtel Money, bank transfer",
    knowsLanguage: "en-UG",
  };
}

/** The website itself, so search engines show the name Finvest for it. */
export function webSite(site: URL): StructuredData {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": new URL("/#website", site).href,
    name: "Finvest",
    url: new URL("/", site).href,
    inLanguage: "en-UG",
    publisher: { "@id": new URL("/#organisation", site).href },
  };
}

/** Questions and answers shown on the page, word for word. */
export function faqPage(questions: readonly { question: string; answer: string }[]): StructuredData {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: questions.map((item) => ({
      "@type": "Question",
      name: item.question,
      acceptedAnswer: { "@type": "Answer", text: item.answer },
    })),
  };
}

/** JSON for a script element: `<` is escaped so no text in it can end the element. */
export function scriptJson(data: StructuredData): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}
