// Record chapters of the product video, in order, on the local stack.
//   node record.mjs 01            record chapter 01 on the stack as it is now
//   node record.mjs 01 --restore  first put the stack back to where chapter 01 starts
//   PV_DRY=1 node record.mjs 01   a quick run with screenshots instead of a recording
// After each chapter the stack is saved as after-<id>, so the next one can be re-recorded alone.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { openContext, closeBrowser } from "./lib/session.mjs";
import { Take } from "./lib/take.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const ids = args.filter((a) => /^\d\d$/.test(a));
const files = fs.readdirSync(path.join(here, "chapters")).filter((f) => f.endsWith(".mjs")).sort();

for (const id of ids) {
  const file = files.find((f) => f.startsWith(id + "-"));
  if (!file) throw new Error(`No chapter ${id}`);
  const chapter = (await import(path.join(here, "chapters", file))).default;
  if (args.includes("--restore")) {
    const i = files.indexOf(file);
    const point = i === 0 ? "start" : `after-${files[i - 1].slice(0, 2)}`;
    execFileSync(path.join(here, "stack/restore.sh"), [point], { stdio: "inherit" });
  }
  if (chapter.before) await chapter.before();
  console.log(`Chapter ${chapter.id}: ${chapter.title}`);
  for (const spec of chapter.takes) {
    // A take can move the story clock first, e.g. { clock: "+1" } for the next morning.
    if (spec.clock) execFileSync(path.join(here, "stack/clock.sh"), [spec.clock], { stdio: "inherit" });
    const session = await openContext(spec.who, spec.layout);
    const t = new Take(session.page, { chapter, name: spec.name, layout: spec.layout });
    try {
      await chapter[spec.name](t, session.page, session);
    } catch (error) {
      await session.page.screenshot({ path: path.join(t.dir, "failed.png") }).catch(() => {});
      console.error(`Chapter ${chapter.id} take ${spec.name} failed after step ${chapter.stepIndex ?? 0}: ${error.message}`);
      process.exitCode = 1;
    }
    await session.close();
    if (process.exitCode) break;
  }
  await closeBrowser();
  if (process.exitCode) break;
  if ((chapter.stepIndex ?? 0) !== chapter.steps.length) throw new Error(`Chapter ${chapter.id} showed ${chapter.stepIndex} of ${chapter.steps.length} steps`);
  // A dry run does the same steps, so its end state is saved too and the next chapter can start.
  execFileSync(path.join(here, "stack/backup.sh"), [`after-${chapter.id}`], { stdio: "inherit" });
}
