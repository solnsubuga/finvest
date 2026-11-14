/**
 * Turns openapi.json into Zod validators and TypeScript types, one file per audience.
 *
 * node scripts/generate.ts          writes src/client.gen.ts and src/staff.gen.ts
 * node scripts/generate.ts --check  fails when either file is out of date
 *
 * The client file holds only what the /client routes use, so a staff-only shape, such as a
 * figure before tax, can never be bundled into the client app. The output depends on the
 * input alone, so the same contract always gives the same files.
 */
import { readFileSync, writeFileSync } from "node:fs";

type Schema = {
  $ref?: string;
  type?: string;
  enum?: (string | number)[];
  const?: string | number | boolean;
  anyOf?: Schema[];
  items?: Schema;
  properties?: Record<string, Schema>;
  required?: string[];
  additionalProperties?: boolean | Schema;
  format?: string;
  contentMediaType?: string;
  default?: unknown;
};

type Parameter = { name: string; in: "path" | "query" | "header" | "cookie"; required?: boolean; schema: Schema };

type Operation = {
  parameters?: Parameter[];
  requestBody?: { content: Record<string, { schema: Schema }>; required?: boolean };
  responses: Record<string, { content?: Record<string, { schema: Schema }> }>;
};

type Contract = {
  paths: Record<string, Record<string, Operation>>;
  components: { schemas: Record<string, Schema> };
};

const METHODS = ["get", "post", "put", "patch", "delete"] as const;
const AUDIENCES = {
  client: (path: string) => path.startsWith("/client/") || path.startsWith("/health/"),
  staff: (path: string) => path.startsWith("/staff/"),
} as const;

const root = new URL("../", import.meta.url);
const contract = JSON.parse(readFileSync(new URL("openapi.json", root), "utf8")) as Contract;
const schemas = contract.components.schemas;

function refName(ref: string): string {
  return ref.slice(ref.lastIndexOf("/") + 1);
}

/** A valid identifier for a schema name such as `Amount-Output` or `app__modules__x__api__Amount`. */
function identifier(name: string): string {
  // Only a module-qualified name drops its prefix. A form body such as
  // `Body_add_photo_client_x__kind__put` keeps its whole name, or two would both become `Put`.
  const parts = name.split("__");
  const base = name.startsWith("app__") && parts.length > 2 ? parts.slice(2).join("_") : name;
  return base
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((word) => word[0]!.toUpperCase() + word.slice(1))
    .join("");
}

function quote(value: unknown): string {
  return JSON.stringify(value);
}

function key(name: string): string {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name) ? name : quote(name);
}

function isNull(schema: Schema): boolean {
  return schema.type === "null";
}

function isFile(schema: Schema): boolean {
  return schema.type === "string" && (schema.format === "binary" || schema.contentMediaType !== undefined);
}

/** The Zod expression for a schema. */
function zod(schema: Schema): string {
  if (schema.$ref) {
    return identifier(refName(schema.$ref));
  }
  if (schema.anyOf) {
    const options = schema.anyOf.filter((option) => !isNull(option));
    const nullable = options.length < schema.anyOf.length;
    const inner = options.length === 1 ? zod(options[0]!) : `z.union([${options.map(zod).join(", ")}])`;
    return nullable ? `${inner}.nullable()` : inner;
  }
  if (schema.const !== undefined) {
    return `z.literal(${quote(schema.const)})`;
  }
  if (schema.enum) {
    if (schema.enum.every((value) => typeof value === "string")) {
      return `z.enum([${schema.enum.map(quote).join(", ")}])`;
    }
    return `z.union([${schema.enum.map((value) => `z.literal(${quote(value)})`).join(", ")}])`;
  }
  switch (schema.type) {
    case "string":
      return isFile(schema) ? "Upload" : "z.string()";
    case "integer":
      return "z.number().int()";
    case "number":
      return "z.number()";
    case "boolean":
      return "z.boolean()";
    case "null":
      return "z.null()";
    case "array":
      return `z.array(${schema.items ? zod(schema.items) : "z.unknown()"})`;
    case "object":
    case undefined:
      return object(schema);
    default:
      throw new Error(`Unsupported schema type ${schema.type}`);
  }
}

function object(schema: Schema): string {
  const properties = Object.entries(schema.properties ?? {});
  if (properties.length === 0) {
    return schema.additionalProperties === false ? "z.object({})" : "z.record(z.string(), z.unknown())";
  }
  const required = new Set(schema.required ?? []);
  const fields = properties.map(([name, property]) => {
    const optional = required.has(name) ? "" : ".optional()";
    return `  ${key(name)}: ${zod(property)}${optional},`;
  });
  return `z.object({\n${fields.join("\n")}\n})`;
}

/** Every schema a schema refers to, directly. */
function references(schema: Schema, found: Set<string> = new Set()): Set<string> {
  if (schema.$ref) {
    found.add(refName(schema.$ref));
  }
  for (const option of schema.anyOf ?? []) references(option, found);
  if (schema.items) references(schema.items, found);
  for (const property of Object.values(schema.properties ?? {})) references(property, found);
  if (typeof schema.additionalProperties === "object") references(schema.additionalProperties, found);
  return found;
}

type Op = {
  name: string;
  method: string;
  path: string;
  pathParams: Parameter[];
  query: Parameter[];
  idempotent: boolean;
  body: { kind: "json" | "multipart"; schema: Schema; required: boolean } | null;
  status: number;
  response: Schema | null;
};

function operationsFor(audience: keyof typeof AUDIENCES): Op[] {
  const ops: Op[] = [];
  for (const path of Object.keys(contract.paths).sort()) {
    if (!AUDIENCES[audience](path)) continue;
    const item = contract.paths[path]!;
    for (const method of METHODS) {
      const operation = item[method];
      if (!operation) continue;
      const parameters = operation.parameters ?? [];
      const success = Object.keys(operation.responses).filter((code) => code.startsWith("2")).sort();
      if (success.length !== 1) throw new Error(`${method} ${path} has ${success.length} success responses`);
      const status = Number(success[0]);
      const content = operation.responses[success[0]!]!.content;
      const json = content?.["application/json"];
      const requestContent = operation.requestBody?.content ?? {};
      const jsonBody = requestContent["application/json"];
      const formBody = requestContent["multipart/form-data"];
      const bodySchema = jsonBody ?? formBody;
      ops.push({
        name: `${method.toUpperCase()} ${path}`,
        method: method.toUpperCase(),
        path,
        pathParams: parameters.filter((p) => p.in === "path"),
        query: parameters.filter((p) => p.in === "query"),
        idempotent: parameters.some((p) => p.in === "header" && p.name.toLowerCase() === "idempotency-key"),
        body: bodySchema
          ? {
              kind: jsonBody ? "json" : "multipart",
              schema: bodySchema.schema,
              required: operation.requestBody?.required ?? false,
            }
          : null,
        status,
        response: status === 204 || !json ? null : json.schema,
      });
    }
  }
  return ops;
}

/** The named schemas the operations need, each after the schemas it uses. */
function orderedSchemas(ops: Op[]): string[] {
  const wanted = new Set<string>();
  const visit = (schema: Schema) => {
    for (const name of references(schema)) {
      if (!wanted.has(name)) {
        wanted.add(name);
        visit(schemas[name]!);
      }
    }
  };
  for (const op of ops) {
    for (const p of [...op.pathParams, ...op.query]) visit(p.schema);
    if (op.body) visit(op.body.schema);
    if (op.response) visit(op.response);
  }
  const ordered: string[] = [];
  const placed = new Set<string>();
  const place = (name: string, trail: string[]) => {
    if (placed.has(name)) return;
    if (trail.includes(name)) throw new Error(`Schemas refer to each other in a loop: ${[...trail, name].join(" -> ")}`);
    for (const dependency of [...references(schemas[name]!)].sort()) place(dependency, [...trail, name]);
    placed.add(name);
    ordered.push(name);
  };
  for (const name of [...wanted].sort()) place(name, []);
  return ordered;
}

function parametersObject(params: Parameter[]): string {
  if (params.length === 0) return "z.object({})";
  const fields = params.map((p) => {
    const optional = p.required ? "" : ".optional()";
    return `    ${key(p.name)}: ${zod(p.schema)}${optional},`;
  });
  return `z.object({\n${fields.join("\n")}\n  })`;
}

function render(audience: keyof typeof AUDIENCES): string {
  const ops = operationsFor(audience);
  const lines: string[] = [
    "// Generated from openapi.json by scripts/generate.ts. Do not edit.",
    "/* eslint-disable */",
    'import { z } from "zod";',
    "",
    "/** A file for a multipart upload: a Blob on the web, a local file description on a phone. */",
    "export type Upload = Blob | { uri: string; name: string; type: string };",
    "export const Upload = z.custom<Upload>((value) => value !== null && typeof value === \"object\");",
    "",
  ];
  for (const name of orderedSchemas(ops)) {
    const id = identifier(name);
    lines.push(`export const ${id} = ${zod(schemas[name]!)};`);
    lines.push(`export type ${id} = z.infer<typeof ${id}>;`);
    lines.push("");
  }
  lines.push("/** Every route, by method and path, with what it takes and what it answers. */");
  lines.push("export const operations = {");
  for (const op of ops) {
    lines.push(`  ${quote(op.name)}: {`);
    lines.push(`    method: ${quote(op.method)},`);
    lines.push(`    path: ${quote(op.path)},`);
    lines.push(`    params: ${parametersObject(op.pathParams)},`);
    lines.push(`    query: ${parametersObject(op.query)},`);
    lines.push(`    body: ${op.body ? zod(op.body.schema) : "null"},`);
    lines.push(`    bodyKind: ${op.body ? quote(op.body.kind) : "null"},`);
    lines.push(`    idempotent: ${op.idempotent},`);
    lines.push(`    status: ${op.status},`);
    lines.push(`    response: ${op.response ? zod(op.response) : "null"},`);
    lines.push("  },");
  }
  lines.push("} as const;");
  lines.push("");
  lines.push("export type Operations = typeof operations;");
  lines.push("export type OperationName = keyof Operations;");
  lines.push("");
  return lines.join("\n");
}

const check = process.argv.includes("--check");
let stale = false;
for (const audience of Object.keys(AUDIENCES) as (keyof typeof AUDIENCES)[]) {
  const file = new URL(`src/${audience}.gen.ts`, root);
  const output = render(audience);
  if (check) {
    let current = "";
    try {
      current = readFileSync(file, "utf8");
    } catch {
      current = "";
    }
    if (current !== output) {
      console.error(`src/${audience}.gen.ts is out of date. Run \`pnpm generate\` in packages/api-client.`);
      stale = true;
    }
  } else {
    writeFileSync(file, output);
  }
}
if (stale) process.exit(1);
