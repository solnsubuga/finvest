# packages/api-client

Zod validators and TypeScript types generated from the backend's OpenAPI description. The client app and console import from here.

Nothing in `src/` is edited by hand. The Pydantic models and routes in `backend/` are the source.

## How it is made

| File | Made by |
| --- | --- |
| `openapi.json` | `uv run python -m app.api.contract` in `backend/`. It builds the app with placeholder settings and never connects to a database. |
| `src/client.gen.ts` | `pnpm generate` here, from the `/client` and `/health` routes only |
| `src/staff.gen.ts` | `pnpm generate` here, from the `/staff` routes only |

`make api-contract` at the repository root runs both steps. Run it after changing a route or a model, and commit what it writes.

Two checks keep it honest:

- A backend unit test fails when `openapi.json` no longer matches the routes.
- The client CI job runs `pnpm check`, which fails when a `.gen.ts` file no longer matches `openapi.json`.

The client file holds only the shapes the client routes use, so a staff-only shape, such as a figure before tax, cannot reach the client app's bundle. The client CI job also greps the production build for `before_tax`.

## Using it

```ts
import { operations, type GoalView } from "@finvest/api-client/client";
```

Each schema is exported twice under one name: as a Zod validator and as its TypeScript type. `operations` lists every route by `"METHOD /path"`, with its path and query parameters, body, whether it takes an `Idempotency-Key`, its success status and its answer. The client app's `src/api/http.ts` reads it to make typed calls and to check every answer before a screen sees it.

The generator is `scripts/generate.ts`, with no dependency beyond Node. It supports the parts of JSON Schema that Pydantic emits here, and stops with an error on anything else rather than guess.
