import { writeFileSync } from "node:fs";
import { toCss } from "../src/css.ts";

writeFileSync(new URL("../tokens.css", import.meta.url), toCss());
