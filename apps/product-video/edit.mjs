// Edit the recorded chapters into the finished video.
//   node edit.mjs 01 02 ...     build those chapters (each: frames, captions, title card, clicks)
//   node edit.mjs --final       join every built chapter, add the outro and the music, and write
//                               introducing-finvest.mp4 and introducing-finvest-no-music.mp4
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WORK } from "./lib/browser.mjs";
import { cards, WIDE, PHONE, CAPTION_Y } from "./lib/cards.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const OUT = process.env.PV_OUT ?? path.join(WORK, "out");
const TITLE_SECONDS = 3.2;
const FADE = 0.6;
fs.mkdirSync(OUT, { recursive: true });

const files = fs.readdirSync(path.join(here, "chapters")).filter((f) => f.endsWith(".mjs")).sort();
async function chapterById(id) {
  const file = files.find((f) => f.startsWith(id + "-"));
  return (await import(path.join(here, "chapters", file))).default;
}
const kickerFor = (c, index) => (index === 0 ? c.kicker : `Part ${index + 1}`);

function ffmpeg(list) {
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...list], { stdio: "inherit" });
}

function hostOf(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" ? u.host : null;
  } catch {
    return null;
  }
}

async function buildChapter(id) {
  const chapter = await chapterById(id);
  const index = files.findIndex((f) => f.startsWith(id + "-"));
  const dir = path.join(WORK, "edit", id);
  execFileSync("python3", ["-I", path.join(here, "edit/timeline.py"), WORK, id, ...chapter.takes.map((t) => t.name)], { stdio: "inherit" });
  const tl = JSON.parse(fs.readFileSync(path.join(dir, "timeline.json"), "utf8"));
  const kicker = kickerFor(chapter, index);

  // Frames: one per take and address shown, as segments in output time.
  const segments = [];
  for (const take of tl.takes) {
    const shown = tl.address.filter((a) => a.t >= take.from - 0.01 && a.t < take.to && hostOf(a.url));
    let host = hostOf(shown[0]?.url) ?? "app.finvestug.com";
    let from = take.from;
    for (const a of shown.slice(1)) {
      const h = hostOf(a.url);
      if (h && h !== host) {
        segments.push({ from, to: a.t, host, layout: take.layout });
        host = h;
        from = a.t;
      }
    }
    segments.push({ from, to: take.to, host, layout: take.layout });
  }
  const frameFiles = [];
  for (const s of segments) {
    const file = path.join(dir, `frame-${s.layout}-${s.host}.png`);
    if (!frameFiles.includes(file)) {
      if (s.layout === "phone") await cards.phoneFrame(s.host, { kicker, title: chapter.title, subtitle: chapter.subtitle }, file);
      else await cards.wideFrame(s.host === "mail.local.test" ? "Test inbox" : s.host, file);
      frameFiles.push(file);
    }
    s.file = file;
  }
  // Captions: each shows from its step until the next one, or the end of the chapter.
  const caps = [];
  for (const [i, c] of tl.captions.entries()) {
    const file = path.join(dir, `caption-${c.n}.png`);
    await cards.caption(c.text, c.n, c.of, file);
    caps.push({ ...c, to: tl.captions[i + 1]?.t ?? tl.duration, file });
  }
  const title = path.join(dir, "title.png");
  await cards.title(kicker, chapter.title, chapter.subtitle, title);

  // Compose each take: its frames under the recording, its captions over it.
  const parts = [];
  for (const take of tl.takes) {
    const len = take.to - take.from;
    const local = (t) => Math.max(0, t - take.from);
    const segs = segments.filter((s) => s.to > take.from + 0.001 && s.from < take.to - 0.001);
    const mine = caps.filter((c) => c.t >= take.from - 0.001 && c.t < take.to);
    const inputs = ["-framerate", "30", "-i", path.join(dir, `seq-${take.name}/%06d.jpg`)];
    const unique = [...new Set(segs.map((s) => s.file))];
    for (const f of unique) inputs.push("-loop", "1", "-framerate", "30", "-t", len.toFixed(3), "-i", f);
    for (const c of mine) inputs.push("-loop", "1", "-framerate", "30", "-t", len.toFixed(3), "-i", c.file);
    const filters = [];
    let base = "[1:v]";
    unique.slice(1).forEach((f, k) => {
      const on = segs.filter((s) => s.file === f).map((s) => `between(t,${local(s.from).toFixed(3)},${local(s.to).toFixed(3)})`).join("+");
      filters.push(`${base}[${k + 2}:v]overlay=0:0:enable='${on}'[bg${k}]`);
      base = `[bg${k}]`;
    });
    if (take.layout === "phone") {
      filters.push(`[0:v]scale=${PHONE.w}:${PHONE.h}:flags=lanczos[vs]`);
      filters.push(`${base}[vs]overlay=${PHONE.x}:${PHONE.y + PHONE.status + PHONE.url}[s]`);
    } else {
      filters.push(`[0:v]scale=${WIDE.w}:${WIDE.h}:flags=lanczos[vs]`);
      filters.push(`${base}[vs]overlay=${WIDE.x}:${WIDE.y}[s]`);
    }
    let last = "[s]";
    mine.forEach((c, k) => {
      const n = 1 + unique.length + k;
      const a = local(c.t), b = Math.min(local(c.to), len);
      filters.push(`[${n}:v]format=rgba,fade=t=in:st=${a.toFixed(3)}:d=0.3:alpha=1,fade=t=out:st=${Math.max(a, b - 0.25).toFixed(3)}:d=0.25:alpha=1[c${k}]`);
      filters.push(`${last}[c${k}]overlay=0:${CAPTION_Y}:enable='between(t,${a.toFixed(3)},${b.toFixed(3)})'[o${k}]`);
      last = `[o${k}]`;
    });
    filters.push(`${last}format=yuv420p,setsar=1[out]`);
    const part = path.join(dir, `part-${take.name}.mp4`);
    ffmpeg([...inputs, "-filter_complex", filters.join(";"), "-map", "[out]", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-t", len.toFixed(3), part]);
    parts.push(part);
  }
  const body = path.join(dir, "body.mp4");
  fs.writeFileSync(path.join(dir, "parts.txt"), parts.map((p) => `file '${p}'`).join("\n") + "\n");
  ffmpeg(["-f", "concat", "-safe", "0", "-i", path.join(dir, "parts.txt"), "-c", "copy", body]);

  // Title card, then a cross-fade into the chapter.
  const full = path.join(OUT, `chapter-${id}.mp4`);
  ffmpeg([
    "-loop", "1", "-framerate", "30", "-t", String(TITLE_SECONDS + FADE), "-i", title,
    "-i", body,
    "-filter_complex", `[0:v]format=yuv420p,setsar=1,settb=1/30,fps=30[t];[1:v]setsar=1,settb=1/30,fps=30[b];[t][b]xfade=transition=fade:duration=${FADE}:offset=${TITLE_SECONDS}[v]`,
    "-map", "[v]", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "18", full,
  ]);
  // Clicks for the sound track, in the chapter's own time (title included).
  const clicks = tl.clicks.map((c) => c + TITLE_SECONDS);
  fs.writeFileSync(path.join(OUT, `chapter-${id}.json`), JSON.stringify({ id, duration: tl.duration + TITLE_SECONDS, clicks, captions: tl.captions }, null, 1));
  console.log(`Built ${full} (${(tl.duration + TITLE_SECONDS).toFixed(1)}s)`);
}

async function final() {
  const built = files.map((f) => f.slice(0, 2)).filter((id) => fs.existsSync(path.join(OUT, `chapter-${id}.mp4`)));
  const outroPng = path.join(OUT, "outro.png");
  await cards.outro("finvestug.com", outroPng);
  const outro = path.join(OUT, "outro.mp4");
  ffmpeg(["-loop", "1", "-framerate", "30", "-t", "5", "-i", outroPng, "-vf", "format=yuv420p,fade=t=in:st=0:d=0.6", "-r", "30", "-c:v", "libx264", "-crf", "18", outro]);
  const list = path.join(OUT, "list.txt");
  fs.writeFileSync(list, [...built.map((id) => `file 'chapter-${id}.mp4'`), "file 'outro.mp4'"].join("\n") + "\n");
  const joined = path.join(OUT, "joined.mp4");
  ffmpeg(["-f", "concat", "-safe", "0", "-i", list, "-c", "copy", joined]);

  // The sound: a quiet click on every click, and an original calm loop under everything.
  let at = 0;
  const clicks = [];
  for (const id of built) {
    const meta = JSON.parse(fs.readFileSync(path.join(OUT, `chapter-${id}.json`), "utf8"));
    for (const c of meta.clicks) clicks.push(at + c);
    at += meta.duration;
  }
  const total = at + 5;
  fs.writeFileSync(path.join(OUT, "clicks.json"), JSON.stringify(clicks));
  execFileSync("python3", ["-I", path.join(here, "edit/sound.py"), OUT, String(total)], { stdio: "inherit" });
  const withMusic = path.join(OUT, "introducing-finvest.mp4");
  const noMusic = path.join(OUT, "introducing-finvest-no-music.mp4");
  ffmpeg(["-i", joined, "-i", path.join(OUT, "sound-music.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", withMusic]);
  ffmpeg(["-i", joined, "-i", path.join(OUT, "sound-clicks.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", noMusic]);
  console.log(`Wrote ${withMusic} and ${noMusic} (${(total / 60).toFixed(1)} min)`);
}

if (args.includes("--final")) await final();
for (const id of args.filter((a) => /^\d\d$/.test(a))) await buildChapter(id);
await cards.close();
