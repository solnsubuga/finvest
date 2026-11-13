# Design

## Canvas

The Finvest 2.0 design canvas is the source of truth for every screen:

https://claude.ai/artifact/BkwR1RQ3VFph43JGX9rbuV

It is private to the project owner. It holds the logo sheet, the client app (sign up, ID check, sign in, invest, goals, withdraw, statements, profile, help), the web layouts, the back office console and the public website.

Build screens to match the canvas. If a screen needs to differ from it, say so in the PR and show both.

## Brand

| Token | Value |
| --- | --- |
| Black | `#0D0D0D` |
| Lime | `#B6EB0A` |
| Ink | `#141414` |
| Muted | `#5E5E5A` |
| Typeface | Plus Jakarta Sans |

The logo mark is a white rising line ending in a lime dot on a black rounded square. The wordmark is "finvest" in lowercase.

These values, and the supporting colours, spacing, radii and type sizes from the canvas, live in code in [`packages/tokens/`](../packages/tokens/README.md). The app, console and website import them from there and never hard-code a brand colour.

## Screenshots

Every PR that adds or changes a screen includes screenshots in its description. See [CONTRIBUTING.md](../CONTRIBUTING.md).
