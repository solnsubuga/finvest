"""Turn the takes of one chapter into an even 30 fps frame sequence and a timed event list.

    python3 -I edit/timeline.py <work dir> <chapter id> <take name> [<take name> ...]

Each take is $WORK/takes/<chapter>-<take>/ with frames/ and take.json (lib/take.mjs). The
screencast sends a frame only when the page changes, so every output frame shows the newest
frame at or before its moment. Waits marked as cuts are left out, sped-up stretches are
shortened, and zooms are drawn here by cropping and scaling, with sub-pixel smoothness.

Writes $WORK/edit/<chapter>/seq-<take>/000001.jpg ... and timeline.json: duration, layout, viewport,
captions with their start times, click times and the address shown over time.
"""

import json
import math
import os
import shutil
import sys
from pathlib import Path

from PIL import Image

FPS = 30
RAMP = 0.6  # seconds to zoom in, and again to zoom out


def segments(events, start, end):
    """(real_from, real_to, speed) pieces covering the take. speed 0 means cut out."""
    marks = []
    for e in events:
        if e["type"] == "cut":
            marks.append((e["t"], "cut", e["on"], 0))
        elif e["type"] == "fast":
            marks.append((e["t"], "fast", e["on"], e.get("factor", 1)))
    marks.sort()
    out, t, cut, fast = [], start, 0, []
    for when, kind, on, factor in marks:
        when = min(max(when, start), end)
        speed = 0 if cut else (fast[-1] if fast else 1)
        if when > t:
            out.append((t, when, speed))
        t = when
        if kind == "cut":
            cut += 1 if on else -1
        elif on:
            fast.append(factor)
        elif fast:
            fast.pop()
    speed = 0 if cut else (fast[-1] if fast else 1)
    if end > t:
        out.append((t, end, speed))
    return out


def to_out(pieces):
    """A function from real time to output time, and the output length."""
    starts, acc = [], 0.0
    for a, b, s in pieces:
        starts.append(acc)
        acc += 0 if s == 0 else (b - a) / s

    def f(t):
        for (a, b, s), o in zip(pieces, starts):
            if t <= b:
                return o if s == 0 else o + (max(t, a) - a) / s
        return acc

    return f, acc, starts


def to_real(pieces, starts, o):
    for (a, b, s), o0 in zip(pieces, starts):
        if s == 0:
            continue
        length = (b - a) / s
        if o <= o0 + length:
            return a + (o - o0) * s
    return pieces[-1][1]


def zoom_at(zooms, o):
    """Zoom factor and centre at output time o."""
    for z in zooms:
        a, b = z["from"], z["to"]
        if a - 0.001 <= o <= b + RAMP:
            if o < a + RAMP:
                k = (o - a) / RAMP
            elif o <= b:
                k = 1
            else:
                k = 1 - (o - b) / RAMP
            k = max(0.0, min(1.0, k))
            k = k * k * (3 - 2 * k)
            return 1 + (z["scale"] - 1) * k, z["cx"], z["cy"]
    return 1, 0, 0


def main():
    work, chapter, takes = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    out_dir = work / "edit" / chapter
    for old in out_dir.glob("seq-*"):
        shutil.rmtree(old)
    timeline = {"chapter": chapter, "takes": [], "captions": [], "clicks": [], "address": [], "sms": []}
    offset = 0.0
    n = 0
    for name in takes:
        d = work / "takes" / f"{chapter}-{name}"
        take = json.loads((d / "take.json").read_text())
        frames = take["frames"]
        if not frames:
            raise SystemExit(f"{d} has no frames")
        events = take["events"]
        start = next(e["t"] for e in events if e["type"] == "start")
        end = next(e["t"] for e in events if e["type"] == "end")
        pieces = segments(events, start, end)
        f, length, starts = to_out(pieces)
        layout = take["layout"]
        vp = take["viewport"]
        timeline["takes"].append({"name": name, "layout": layout, "viewport": vp, "from": offset, "to": offset + length})
        if "layout" not in timeline:
            timeline["layout"] = layout

        zooms, open_zoom = [], None
        for e in events:
            if e["type"] == "zoom" and e["on"]:
                r = e["rect"]
                open_zoom = {"from": f(e["t"]), "scale": e["scale"], "cx": r["x"] + r["width"] / 2, "cy": r["y"] + r["height"] / 2}
            elif e["type"] == "zoom" and open_zoom:
                open_zoom["to"] = f(e["t"]) - RAMP
                zooms.append(open_zoom)
                open_zoom = None
        for e in events:
            if e["type"] == "caption":
                timeline["captions"].append({"t": offset + f(e["t"]), "n": e["n"], "of": e["of"], "text": e["text"]})
            elif e["type"] == "click":
                timeline["clicks"].append(offset + f(e["t"]))
            elif e["type"] == "address":
                timeline["address"].append({"t": offset + f(e["t"]), "url": e["url"], "layout": layout})
            elif e["type"] == "sms":
                timeline["sms"].append({"t": offset + f(e["t"]), "text": e["text"], "for": e.get("for", 4)})

        count = int(math.floor(length * FPS))
        seq = out_dir / f"seq-{name}"
        seq.mkdir(parents=True)
        n = 0
        fi = 0
        scale = 2 if layout == "phone" else 1
        for i in range(count):
            o = i / FPS
            real = to_real(pieces, starts, o)
            while fi + 1 < len(frames) and frames[fi + 1]["t"] <= real:
                fi += 1
            src = d / "frames" / frames[fi]["file"]
            n += 1
            dst = seq / f"{n:06d}.jpg"
            z, cx, cy = zoom_at(zooms, o)
            if z <= 1.0005:
                os.link(src, dst)
            else:
                img = Image.open(src)
                w, h = img.size
                cx, cy = cx * scale, cy * scale
                cw, ch = w / z, h / z
                x0 = min(max(cx - cw / 2, 0), w - cw)
                y0 = min(max(cy - ch / 2, 0), h - ch)
                img = img.transform((w, h), Image.Transform.EXTENT, (x0, y0, x0 + cw, y0 + ch), Image.Resampling.BICUBIC)
                img.save(dst, quality=93)
        timeline["takes"][-1]["to"] = offset + count / FPS
        offset += count / FPS
    timeline["duration"] = offset
    (out_dir / "timeline.json").write_text(json.dumps(timeline, indent=1))
    print(f"{chapter}: {offset:.1f}s, {len(timeline['captions'])} captions")


if __name__ == "__main__":
    main()
