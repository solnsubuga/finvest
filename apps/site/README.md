# apps/site

The public Finvest website at finvestug.com. Astro, built to plain static files.

**Status:** every page on row 8 of the canvas is built: home, products and rates, how it works, safety, about and contact, the help centre, and six legal pages that hold structure only.

- It holds no client data and has no sign-in. A problem here cannot touch accounts.
- It links to the client app. It never links to the back office, and a test fails the build output if it does.
- It loads nothing from another site: the font is served from the site itself.
- Build to the [design canvas](../../docs/design.md), row 8. Every PR that adds or changes a page includes screenshots.

## Commands

Run `pnpm install` once at the repository root, then from this directory:

| Command | What it does |
| --- | --- |
| `pnpm dev` | Serves the site at http://localhost:4321 and reloads on change |
| `pnpm build` | Builds the static site into `dist/` |
| `pnpm preview` | Serves `dist/` as built |
| `pnpm typecheck` | `astro check`, with TypeScript at its strictest |
| `pnpm test` | Unit tests for rates, money and the estimate |
| `pnpm test:build` | Checks on the built pages in `dist/`. Run `pnpm build` first. |
| `pnpm placeholders` | Lists the claims nobody has signed off, and any `[BRACKETED]` placeholder that has crept back in. `--strict` fails if either list has anything in it. |

## Settings

All are public and read at build time. None is a secret.

| Name | Default | Use |
| --- | --- | --- |
| `PUBLIC_SITE_URL` | `https://finvestug.com` | The site's own address, for canonical links |
| `PUBLIC_APP_URL` | `https://app.finvestug.com` | Where "Log in" and "Open an account" go |
| `PUBLIC_API_URL` | `https://api.finvestug.com` | Where the browser reads rates and quotes |
| `PUBLIC_INDEXABLE` | `false` | `true` lets search engines in. Leave it off until `pnpm placeholders --strict` passes. |

## Rates

The pages are built with the rates in `src/data/fallback.ts`, so they show at once and still show if the API is down. When a page opens, the browser asks `GET {API}/public/products` and replaces the rates, minimums, the "last updated" date and whether each product is open, wherever an element carries `data-rate`, `data-minimum`, `data-updated`, `data-when-live` or `data-when-paused`. An answer that is not exactly the expected shape is ignored (`src/lib/products.ts`).

- When an approved rate changes, update `src/data/fallback.ts` too, so the built-in figures do not drift.
- A product the fallback does not list is not shown. Adding a product means adding it there.
- A paused product is shown as "Coming soon" with no button to invest.
- The site only ever holds the yearly rate after tax. It must never carry a rate before tax or a tax rate ([docs/rules.md](../../docs/rules.md)), and a test on the build checks for that.

## The calculator

It answers at once with an estimate worked out from the rate on the page, in whole numbers, rounded down. Then it asks `GET {API}/public/quote` for the exact figure and shows that instead. If the API cannot be reached the estimate stays, and the wording says "about" and "an estimate". The two can differ by a few shillings, because the exact formula works from terms the public site does not carry. See [docs/investing.md](../../docs/investing.md).

Without JavaScript the calculator is a worked example for UGX 1,000,000.

## No placeholders

The site shows no placeholder of any kind (the owner's rule). `pnpm test:build` fails on a `[BRACKETED]` word, and on inline styles, which the hosting's content security policy refuses.

`src/data/company.ts` holds what the pages say about Finvest in more than one place:

- Service promises chosen for now, such as how long an identity check takes and how many working days a complaint takes. Change them there.
- Facts only Finvest can supply: phone number, office address, registered company name, regulator, licence number, custodian bank and auditor. Each is `null` until Finvest gives it, and while it is `null` the pages leave that line out. Fill one in and the line appears in the footer, on Safety, Home, Help and About. Never fill one with a guess: a made-up licence or phone number on a public page is worse than a missing one.

The registered company name, regulator and licence number must be filled in before launch: `pnpm placeholders` lists them while they are empty, and `--strict` fails.

## Claims to sign off before launch

Some statements are written out in full because the page needs them, but the owner and counsel must still confirm they are true: that there is no fee to invest, that bills are held in each client's name, that the books are checked daily, and the rest of `scripts/claims.json`. `pnpm placeholders` lists every claim that has no `confirmedBy`, and `--strict` fails while any is open. When you add a statement of this kind to a page, add it to that file.

## Help centre, contact form and legal pages

- The help centre's questions are in `src/data/help.ts`. The search box filters the questions on the page and needs no server.
- The contact form sends nothing itself, because the site has no server and stores nothing. It opens the visitor's own email app with the message addressed to support, and says so under the button.
- The legal pages are in `src/data/legal.ts`. They hold headings and a line on what each section will cover. The wording comes from Finvest's lawyers, and each page carries a notice saying so.
- Each legal page has a "Download as PDF" link to `/legal/<slug>.pdf`. The PDFs are made at build time by `src/pages/legal/[doc].pdf.ts` with pdf-lib (`src/lib/legal-pdf.ts`), from the same structure and with the same notice, so the link works without JavaScript.

## Layout

| Path | Contents |
| --- | --- |
| `src/pages/` | One file per page |
| `src/layouts/Base.astro` | The page shell: head, header, footer |
| `src/components/` | Header, footer, product card, calculator and the other shared parts |
| `src/styles/site.css` | All styles. Colours, spacing and weights come from `@finvest/tokens`. |
| `src/lib/` | Pure TypeScript: the check on what the API sends, money and dates, the estimate |
| `src/scripts/` | The two browser scripts: live rates and the calculator |
| `src/data/` | The built-in rates, the help centre's questions and the legal pages' structure |
| `tests/` | Checks on the built pages |

## Hosting

`dist/` is plain files: a folder per page, hashed assets under `/_astro/`, and `404.html`. The infrastructure thread serves it (see [infra/README.md](../../infra/README.md)).
