# apps/site

The public Finvest website at finvestug.com.

**Status:** not scaffolded yet.

## Stack

Astro, built as a static site. It reads live rates from a public read-only rates endpoint on the backend.

## Notes

- The site holds no client data and has no sign-in. A problem here cannot touch accounts.
- It must be fast on slow connections.
- It links to the client app. It never links to the back office.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a page includes screenshots.
