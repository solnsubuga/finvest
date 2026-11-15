"""The video's sound: a quiet click under every click, and an original calm music loop.

    python3 -I edit/sound.py <out dir> <seconds>

Reads clicks.json (seconds) from the out dir. Writes sound-clicks.wav (clicks only) and
sound-music.wav (clicks plus music). The music is made here from sine tones: a slow
four-chord pad with a soft pulse, mixed to about -40 dB on average, fading in over 2 seconds
and out over 4, so a voice-over can sit well above it.
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 48000


def click(volume_db=-30.0):
    n = int(0.05 * RATE)
    t = np.arange(n) / RATE
    tone = np.sin(2 * np.pi * 2300 * t) * 0.6 + np.sin(2 * np.pi * 1200 * t) * 0.4
    env = np.exp(-t * 90)
    noise = np.random.default_rng(1).normal(0, 0.25, n) * np.exp(-t * 300)
    c = (tone * env + noise) * 10 ** (volume_db / 20)
    return c


def pad(seconds):
    # Cmaj9, Am7, Fmaj7, G6: four bars of eight seconds each, looping.
    chords = [
        [130.81, 196.00, 246.94, 293.66, 329.63],
        [110.00, 164.81, 196.00, 261.63, 329.63],
        [87.31, 174.61, 220.00, 261.63, 329.63],
        [98.00, 146.83, 196.00, 246.94, 329.63],
    ]
    bar = 8.0
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    out = np.zeros(n)
    for i in range(int(seconds // bar) + 1):
        start = int(i * bar * RATE)
        if start >= n:
            break
        length = min(int((bar + 2) * RATE), n - start)
        tt = np.arange(length) / RATE
        env = np.minimum(1, tt / 2.5) * np.clip((bar + 2 - tt) / 2.5, 0, 1)
        voice = np.zeros(length)
        for f in chords[i % 4]:
            for detune in (-0.6, 0.6):
                voice += np.sin(2 * np.pi * (f + detune) * tt + i)
        out[start : start + length] += voice * env
    # A soft pulse on the root, two beats a bar, like a distant marimba.
    for k in range(int(seconds / 2)):
        start = int(k * 2 * RATE)
        length = min(int(1.2 * RATE), n - start)
        if length <= 0:
            break
        tt = np.arange(length) / RATE
        f = chords[int(k * 2 // bar) % 4][0] * 2
        out[start : start + length] += 0.8 * np.sin(2 * np.pi * f * tt) * np.exp(-tt * 4)
    # Smooth the edges a little: a one-pole low-pass.
    smoothed = np.empty_like(out)
    acc = 0.0
    a = 0.08
    for i in range(0, n, RATE):
        seg = out[i : i + RATE]
        res = np.empty_like(seg)
        for j, x in enumerate(seg):
            acc += a * (x - acc)
            res[j] = acc
        smoothed[i : i + RATE] = res
    return smoothed


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)


def write(path, x):
    x = np.clip(x, -1, 1)
    data = (x * 32767).astype("<i2")
    stereo = np.repeat(data[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(stereo.tobytes())


def main():
    out = Path(sys.argv[1])
    seconds = float(sys.argv[2])
    n = int(seconds * RATE)
    clicks = np.zeros(n)
    c = click()
    for s in json.loads((out / "clicks.json").read_text()):
        i = int(s * RATE)
        if i + len(c) < n:
            clicks[i : i + len(c)] += c
    music = pad(seconds)
    music *= 10 ** ((-40 - rms_db(music)) / 20)
    fade_in = np.minimum(1, np.arange(n) / (2 * RATE))
    fade_out = np.clip((n - np.arange(n)) / (4 * RATE), 0, 1)
    music *= fade_in * fade_out
    write(out / "sound-clicks.wav", clicks)
    write(out / "sound-music.wav", clicks + music)
    print(f"music {rms_db(music):.1f} dB average, clicks peak {20*np.log10(np.max(np.abs(clicks))+1e-12):.1f} dB")


if __name__ == "__main__":
    main()
