"""The video's music: an original, calm, upbeat piece written here as MIDI and played with FluidSynth.

    python3 -I edit/music.py <out wav> <seconds> [seed]

Electric piano chords, a warm pad, a round bass, light drums and a kalimba tune, at 96 beats a
minute. It moves through sections of eight bars. Each section picks its chords, its key and how
many instruments play, so the half hour never loops. The tune is made fresh for every section
from the chords' own notes. It starts with the pad alone and ends on a held chord.

Needs fluidsynth and the FluidR3 General MIDI sound font (Debian packages `fluidsynth` and
`fluid-soundfont-gm`, MIT licence) and the `mido` Python package.
"""

import random
import subprocess
import sys
import tempfile
from pathlib import Path

import mido

BPM = 96
TPB = 480  # ticks per beat
BAR = 4 * TPB
SOUNDFONT = "/usr/share/sounds/sf2/FluidR3_GM.sf2"

# Channels and General MIDI programs.
PIANO, PAD, BASS, TUNE, DRUMS = 0, 1, 2, 3, 9
PROGRAMS = {PIANO: 4, PAD: 89, BASS: 33, TUNE: 108}  # electric piano 1, warm pad, finger bass, kalimba

QUALITIES = {
    "maj7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10],
    "add9": [0, 4, 7, 14],
    "sus": [0, 5, 7, 10],
    "6": [0, 4, 7, 9],
}
# Four-bar progressions, as (semitones above the key, quality); each bar holds one chord.
PROGRESSIONS = [
    [(0, "maj7"), (9, "m7"), (5, "maj7"), (7, "sus")],
    [(9, "m7"), (5, "maj7"), (0, "add9"), (7, "add9")],
    [(5, "maj7"), (7, "add9"), (4, "m7"), (9, "m7")],
    [(2, "m7"), (7, "sus"), (0, "maj7"), (5, "6")],
    [(0, "add9"), (4, "m7"), (5, "maj7"), (7, "add9")],
]
KEYS = [0, 2, 5, 3, 7, -2]  # C, D, F, E flat, G, B flat
# How full each section is: 0 pad and piano, 1 adds bass and tune, 2 adds light drums, 3 all.
SHAPE = [1, 2, 3, 3, 2, 3, 1, 2, 3, 2]


class Track:
    def __init__(self):
        self.events = []  # (tick, order, message)

    def note(self, ch, tick, length, pitch, vel):
        pitch = max(0, min(127, pitch))
        vel = max(1, min(127, int(vel)))
        self.events.append((tick, 1, mido.Message("note_on", channel=ch, note=pitch, velocity=vel)))
        self.events.append((tick + max(1, length), 0, mido.Message("note_off", channel=ch, note=pitch, velocity=0)))

    def to_midi(self, path):
        mid = mido.MidiFile(ticks_per_beat=TPB)
        tr = mido.MidiTrack()
        mid.tracks.append(tr)
        tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))
        for ch, prog in PROGRAMS.items():
            tr.append(mido.Message("program_change", channel=ch, program=prog, time=0))
        for ch, vol, rev in ((PIANO, 92, 50), (PAD, 70, 80), (BASS, 96, 20), (TUNE, 78, 60), (DRUMS, 70, 30)):
            tr.append(mido.Message("control_change", channel=ch, control=7, value=vol, time=0))
            tr.append(mido.Message("control_change", channel=ch, control=91, value=rev, time=0))
        tr.append(mido.Message("control_change", channel=PIANO, control=10, value=54, time=0))
        tr.append(mido.Message("control_change", channel=TUNE, control=10, value=76, time=0))
        last = 0
        for tick, _, msg in sorted(self.events, key=lambda e: (e[0], e[1])):
            tr.append(msg.copy(time=tick - last))
            last = tick
        mid.save(path)


def chord_notes(key, root, quality, low=55):
    base = 48 + (key + root) % 12
    notes = [base + i for i in QUALITIES[quality]]
    # Voice the piano chord between `low` and about an octave and a half above.
    return sorted(n + 12 * ((low - n + 11) // 12) for n in notes)


def section(t, rng, start, key, prog, level, first=False):
    """Eight bars from `start`: the four-bar progression twice, the tune answering itself."""
    motif = None
    for bar in range(8):
        tick = start + bar * BAR
        root, quality = prog[bar % 4]
        voicing = chord_notes(key, root, quality)
        bass_root = 36 + (key + root) % 12
        # Pad: the whole bar, softly.
        for n in chord_notes(key, root, quality, low=48)[:3]:
            t.note(PAD, tick, BAR, n, 46 if level else 40)
        # Piano: held in the quiet sections, a gentle off-beat comp otherwise.
        if level == 0 or first:
            for n in voicing:
                t.note(PIANO, tick, BAR - 40, n, 52 + rng.randint(-4, 4))
        else:
            for beat, length, vel in ((0, 700, 60), (1.5, 340, 48), (2.5, 600, 54), (3.5, 200, 42)):
                if beat == 3.5 and rng.random() < 0.5:
                    continue
                for n in voicing:
                    t.note(PIANO, tick + int(beat * TPB) + rng.randint(0, 12), length, n, vel + rng.randint(-5, 5))
        if level >= 1 and not first:
            # Bass: the root, a push before beat three, then the fifth or the octave.
            fifth = bass_root + (7 if quality != "sus" else 5)
            pattern = [(0, 900, bass_root, 84), (1.75, 220, bass_root, 64), (2.5, 600, rng.choice([fifth, bass_root + 12]), 74)]
            if bar % 4 == 3:
                pattern.append((3.5, 220, bass_root + rng.choice([2, -2, 5]), 62))
            for beat, length, n, vel in pattern:
                t.note(BASS, tick + int(beat * TPB), length, n, vel)
        if level >= 2 and not first:
            for b in range(4):
                bt = tick + b * TPB
                if b in (0, 2):
                    t.note(DRUMS, bt, 120, 36, 70 if b == 0 else 58)
                if b in (1, 3):
                    t.note(DRUMS, bt, 120, 37 if level == 2 else 38, 44 if level == 2 else 40)
                for half in (0, 1):
                    t.note(DRUMS, bt + half * TPB // 2 + (18 if half else 0), 60, 42, (40 if half else 50) + rng.randint(-6, 6))
            if level >= 3:
                t.note(DRUMS, tick + int(2.5 * TPB), 120, 36, 50)
                for s in range(16):
                    t.note(DRUMS, tick + s * TPB // 4, 40, 70, 22 + (8 if s % 4 == 0 else 0) + rng.randint(-4, 4))
            if bar == 7 and level >= 3:
                t.note(DRUMS, tick + int(3.5 * TPB), 300, 57, 34)  # a soft crash into the next section
        if level >= 1 and not first:
            # Tune: a two-bar phrase on the chord and its pentatonic neighbours, then an answer.
            scale = sorted({(key + s) % 12 for s in (0, 2, 4, 7, 9)} | {(key + root + i) % 12 for i in QUALITIES[quality][:3]})
            pool = [p for p in range(67, 86) if p % 12 in scale]
            chord_pcs = {(key + root + i) % 12 for i in QUALITIES[quality][:3]}
            if bar % 2 == 0:
                if motif is None or bar % 4 == 0:
                    rhythm = rng.choice([[0, 1, 1.5, 2.5], [0, 0.5, 1, 2, 3], [0.5, 1, 2, 2.5, 3.5], [0, 1.5, 2, 3]])
                    contour = [rng.choice([-2, -1, 1, 2]) for _ in rhythm]
                    motif = (rhythm, contour)
            rhythm, contour = motif
            if bar % 4 == 3 and rng.random() < 0.6:
                continue  # leave room at the end of a phrase
            idx = rng.randrange(len(pool) // 3, 2 * len(pool) // 3)
            for i, (beat, step) in enumerate(zip(rhythm, contour)):
                idx = max(0, min(len(pool) - 1, idx + (step if bar % 2 == 0 else -step)))
                n = pool[idx]
                if beat in (0, 2) and n % 12 not in chord_pcs:
                    n = min(pool, key=lambda p: (p % 12 not in chord_pcs, abs(p - n)))
                t.note(TUNE, tick + int(beat * TPB), 420, n, 62 + rng.randint(-6, 6) + (6 if i == 0 else 0))


def compose(seconds, seed):
    rng = random.Random(seed)
    t = Track()
    total_bars = int(seconds * BPM / 60 / 4) + 1
    sections = total_bars // 8 + 1
    key = 0
    for s in range(sections):
        if s and s % 3 == 0:
            key = rng.choice([k for k in KEYS if k != key])
        prog = rng.choice(PROGRESSIONS)
        level = 0 if s == 0 else SHAPE[s % len(SHAPE)]
        section(t, rng, s * 8 * BAR, key, prog, level, first=(s == 0))
    end = sections * 8 * BAR
    root, quality = (0, "add9")
    for n in chord_notes(key, root, quality):
        t.note(PIANO, end, 6 * TPB, n, 54)
    for n in chord_notes(key, root, quality, low=48)[:3]:
        t.note(PAD, end, 8 * TPB, n, 44)
    return t


def main():
    out = Path(sys.argv[1])
    seconds = float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 2027
    with tempfile.TemporaryDirectory() as tmp:
        mid = Path(tmp) / "music.mid"
        compose(seconds + 8, seed).to_midi(mid)
        subprocess.run(
            ["fluidsynth", "-ni", "-q", "-g", "0.6", "-r", "48000", "-F", str(out), SOUNDFONT, str(mid)],
            check=True,
        )


if __name__ == "__main__":
    main()
