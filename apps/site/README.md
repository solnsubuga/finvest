# apps/site

The public Finvest website at finvestug.com. Astro, built to plain static files.

**Status:** home, and products and rates. How it works, safety, about and contact, the help centre and the legal pages follow in the next pull request.

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
| `pnpm placeholders` | Lists the `[BRACKETED]` facts still missing. `--strict` fails if any are left. |

## Settings

All are public and read at build time. None is a secret.

| Name | Default | Use |
| --- | --- | --- |
| `PUBLIC_SITE_URL` | `https://finvestug.com` | The site's own address, for canonical links |
| `PUBLIC_APP_URL` | `https://app.finvestug.com` | Where "Log in" and "Open an account" go |
| `PUBLIC_API_URL` | `https://api.finvestug.com` | Where the browser reads rates and quotes |
| `PUBLIC_INDEXABLE` | `false` | `true` lets search engines in. Leave it off until `pnpm placeholders` finds nothing. |

## Rates

The pages are built with the rates in `src/data/fallback.ts`, so they show at once and still show if the API is down. When a page opens, the browser asks `GET {API}/public/products` and replaces the rates, minimums, the "last updated" date and whether each product is open, wherever an element carries `data-rate`, `data-minimum`, `data-updated`, `data-when-live` or `data-when-paused`. An answer that is not exactly the expected shape is ignored (`src/lib/products.ts`).

- When an approved rate changes, update `src/data/fallback.ts` too, so the built-in figures do not drift.
- A product the fallback does not list is not shown. Adding a product means adding it there.
- A paused product is shown as "Coming soon" with no button to invest.
- The site only ever holds the yearly rate after tax. It must never carry a rate before tax or a tax rate ([docs/rules.md](../../docs/rules.md)), and a test on the build checks for that.

## The calculator

It answers at once with an estimate worked out from the rate on the page, in whole numbers, rounded down. Then it asks `GET {API}/public/quote` for the exact figure and shows that instead. If the API cannot be reached the estimate stays, and the wording says "about" and "an estimate". The two can differ by a few shillings, because the exact formula works from terms the public site does not carry. See [docs/investing.md](../../docs/investing.md).

Without JavaScript the calculator is a worked example for UGX 1,000,000.

## Placeholders

Facts Finvest has not supplied yet are written as `[SQUARE BRACKETS]`: the regulator and licence number, the custodian bank, the office address, fees. Do not replace one with a guess. `pnpm placeholders` lists what is left.

## Layout

| Path | Contents |
| --- | --- |
| `src/pages/` | One file per page |
| `src/layouts/Base.astro` | The page shell: head, header, footer |
| `src/components/` | Header, footer, product card, calculator and the other shared parts |
| `src/styles/site.css` | All styles. Colours, spacing and weights come from `@finvest/tokens`. |
| `src/lib/` | Pure TypeScript: the check on what the API sends, money and dates, the estimate |
| `src/scripts/` | The two browser scripts: live rates and the calculator |
| `src/data/fallback.ts` | The built-in rates |
| `tests/` | Checks on the built pages |

## Hosting

`dist/` is plain files: a folder per page, hashed assets under `/_astro/`, and `404.html`. The infrastructure thread serves it (see [infra/README.md](../../infra/README.md)).
