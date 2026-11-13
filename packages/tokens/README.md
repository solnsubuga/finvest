# packages/tokens

Shared design tokens: colour, type, spacing and radii. The client app, console and website import brand values from here and never hard-code them.

The values come from the [design canvas](../../docs/design.md).

## Using it

Add the dependency in the app's `package.json`:

```json
"@finvest/tokens": "workspace:*"
```

In TypeScript, including React Native:

```ts
import { colors, spacing, radii, fontFamily, fontSizes, fontWeights } from "@finvest/tokens";
```

Every value is a plain string or number. Sizes are numbers in pixels, not `"16px"` strings.

On the web, the same values are CSS custom properties with an `--fv-` prefix:

```css
@import "@finvest/tokens/tokens.css";

.title {
  color: var(--fv-color-ink);
  font-size: var(--fv-font-size-3xl);
}
```

There is no build step. The package exports its TypeScript source, which Vite, Metro and Astro all read directly.

## Changing a token

1. Edit `src/index.ts`.
2. Run `pnpm build:css` to regenerate `tokens.css`.
3. Commit both files. A test fails if `tokens.css` does not match the source.

## Commands

Run from this directory.

| Command | What it does |
| --- | --- |
| `pnpm build:css` | Regenerates `tokens.css` from `src/index.ts` |
| `pnpm typecheck` | Type checks the package |
| `pnpm test` | Runs the unit tests |
