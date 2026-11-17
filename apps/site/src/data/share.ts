/**
 * The pictures shown when a link to the site is shared in WhatsApp, Slack, X, LinkedIn and the
 * like (the page's og:image). One 1200 by 630 PNG per entry, in public/share/, drawn from these
 * words by `pnpm share-images`. Change the words here, run it, and commit the new pictures.
 *
 * The words are the page's own heading, so the picture matches the page it opens. No rate or other
 * figure that changes goes on a picture: a shared picture is cached for weeks by the apps that
 * show it.
 */

export interface ShareImage {
  /** File name in public/share/, without .png. */
  name: string;
  /** Small capitals above the heading. Left out on the home picture. */
  eyebrow: string | null;
  /** The heading. */
  headline: string;
  /** The end of the heading, drawn in lime. */
  highlight: string | null;
  /** What the picture shows, for people who cannot see it (og:image:alt). */
  alt: string;
}

export const shareImages = {
  home: {
    name: "home",
    eyebrow: null,
    headline: "Your money, working",
    highlight: "every day.",
    alt: "Finvest: your money, working every day. Invest in Uganda government Treasury bills and bonds from your phone.",
  },
  products: {
    name: "products",
    eyebrow: "Products",
    headline: "Simple investments, with the return shown up front.",
    highlight: null,
    alt: "Finvest products: simple investments, with the return shown up front.",
  },
  how: {
    name: "how-it-works",
    eyebrow: "How it works",
    headline: "From sign up to payout, without visiting an office.",
    highlight: null,
    alt: "How Finvest works: from sign up to payout, without visiting an office.",
  },
  safety: {
    name: "safety",
    eyebrow: "Safety",
    headline: "Where your money goes, and who looks after it.",
    highlight: null,
    alt: "Finvest safety: where your money goes, and who looks after it.",
  },
  about: {
    name: "about",
    eyebrow: "About",
    headline: "Treasury bills and bonds used to be for banks and big investors.",
    highlight: "Not any more.",
    alt: "About Finvest: Treasury bills and bonds used to be for banks and big investors. Not any more.",
  },
  help: {
    name: "help",
    eyebrow: "Help centre",
    headline: "How can we help?",
    highlight: null,
    alt: "Finvest help centre: how can we help?",
  },
  check: {
    name: "check",
    eyebrow: "Check a statement",
    headline: "Is this Finvest statement genuine?",
    highlight: null,
    alt: "Check a Finvest statement: is this Finvest statement genuine?",
  },
} as const satisfies Record<string, ShareImage>;

export type ShareImageKey = keyof typeof shareImages;

/** The line under every heading: what Finvest offers, in the home page's words. */
export const shareTagline = "Uganda Treasury bills and bonds, goals and automatic investing.";

export const shareImageWidth = 1200;
export const shareImageHeight = 630;

/** The crawlers of the apps that draw a preview of a shared link (robots.txt lets them in). */
export const linkPreviewAgents = [
  "facebookexternalhit",
  "Facebot",
  "WhatsApp",
  "Twitterbot",
  "LinkedInBot",
  "Slackbot-LinkExpanding",
  "Slackbot",
  "TelegramBot",
  "Discordbot",
] as const;
