#!/usr/bin/env python3
"""Procedural ~15s retro synthwave loop (no samples). Writes WAV at argv[1]."""
import math
import sys
import wave

import numpy as np

SR = 44100
BPM = 105
BEAT = 60.0 / BPM
DURATION = 15.0
OUT = sys.argv[1] if len(sys.argv) > 1 else "music.wav"


def adsr(t, a, d, s, r, total):
    env = np.zeros_like(t)
    for i, ti in enumerate(t):
        if ti < a:
            env[i] = ti / a
        elif ti < a + d:
            env[i] = 1.0 - (1.0 - s) * (ti - a) / d
        elif ti < total - r:
            env[i] = s
        else:
            env[i] = s * max(0.0, (total - ti) / r)
    return env


def sine(freq, t, phase=0.0):
    return np.sin(2 * math.pi * freq * t + phase)


def square(freq, t, duty=0.5):
    return np.where((t * freq) % 1.0 < duty, 1.0, -1.0)


def noise(n):
    return np.random.uniform(-1, 1, n)


def soft_clip(x, drive=1.4):
    return np.tanh(x * drive)


def main():
    n = int(DURATION * SR)
    t = np.arange(n, dtype=np.float64) / SR
    rng = np.random.default_rng(42)

    # ---- drums ----
    kick = np.zeros(n)
    snare = np.zeros(n)
    hat = np.zeros(n)
    steps_per_beat = 4
    step = BEAT / steps_per_beat
    for beat_i in range(int(DURATION / BEAT) + 2):
        bt = beat_i * BEAT
        ki = int(bt * SR)
        if 0 <= ki < n:
            kt = np.arange(min(int(0.18 * SR), n - ki)) / SR
            kick[ki : ki + len(kt)] += 0.95 * np.sin(2 * math.pi * (55 + 80 * np.exp(-kt * 28)) * kt) * np.exp(-kt * 14)
        if beat_i % 2 == 1:
            si = int(bt * SR)
            if 0 <= si < n:
                sn = rng.normal(0, 1, min(int(0.12 * SR), n - si))
                sn *= np.exp(-np.arange(len(sn)) / (0.018 * SR))
                snare[si : si + len(sn)] += 0.42 * sn
        for s in range(steps_per_beat):
            st = bt + s * step
            if s % 2 == 1:
                hi = int(st * SR)
                if 0 <= hi < n:
                    hn = rng.normal(0, 1, min(int(0.025 * SR), n - hi))
                    hn *= np.exp(-np.arange(len(hn)) / (0.004 * SR))
                    hat[hi : hi + len(hn)] += 0.11 * hn

    # ---- bass (root progression: Am -> F -> C -> G) ----
    roots = [55.0, 43.65, 65.41, 49.0]  # A1, F1, C2, G1
    bass = np.zeros(n)
    bar = 4 * BEAT
    for bar_i in range(int(DURATION / bar) + 1):
        f0 = roots[bar_i % len(roots)]
        for eighth in range(8):
            onset = bar_i * bar + eighth * (BEAT / 2)
            if onset >= DURATION:
                break
            idx = int(onset * SR)
            seg = int(0.11 * SR)
            if idx + seg > n:
                seg = n - idx
            if seg <= 0:
                continue
            local = np.arange(seg) / SR
            env = np.exp(-local * 22)
            wave_b = sine(f0, local) * 0.55 + sine(f0 * 2, local) * 0.12
            bass[idx : idx + seg] += wave_b * env

    # ---- arpeggio (square, 16th notes) ----
    arp_notes = [220, 261.63, 329.63, 392.0, 329.63, 261.63]
    arp = np.zeros(n)
    sixteenth = BEAT / 4
    for i in range(int(DURATION / sixteenth)):
        onset = i * sixteenth
        freq = arp_notes[i % len(arp_notes)]
        idx = int(onset * SR)
        seg = int(sixteenth * 0.7 * SR)
        if idx + seg > n:
            seg = n - idx
        if seg <= 0:
            continue
        local = np.arange(seg) / SR
        env = np.exp(-local * 9)
        arp[idx : idx + seg] += square(freq, local, 0.35) * env * 0.09

    # ---- pad (detuned saws via additive sines) ----
    pad = np.zeros(n)
    chord_sets = [(220, 261.63, 329.63), (174.61, 220, 261.63), (130.81, 164.81, 196.0), (196.0, 246.94, 293.66)]
    for bar_i in range(int(DURATION / bar) + 1):
        onset = bar_i * bar
        freqs = chord_sets[bar_i % len(chord_sets)]
        end = min(DURATION, onset + bar * 0.95)
        idx0 = int(onset * SR)
        idx1 = int(end * SR)
        if idx1 <= idx0:
            continue
        lt = np.arange(idx1 - idx0) / SR
        env = adsr(lt, 0.08, 0.35, 0.55, 0.25, end - onset)
        layer = sum(sine(f, lt) + 0.5 * sine(f * 1.006, lt) for f in freqs) / (len(freqs) * 1.5)
        pad[idx0:idx1] += layer * env * 0.14

    mix = kick * 0.9 + snare + hat + bass + arp + pad
    fade = min(int(1.2 * SR), n // 4)
    ramp = np.ones(n)
    ramp[:fade] *= np.linspace(0, 1, fade)
    ramp[-fade:] *= np.linspace(1, 0, fade)
    mix *= ramp
    mix = soft_clip(mix, 1.25)
    peak = np.max(np.abs(mix)) or 1.0
    mix = mix / peak * 0.92
    pcm = (mix * 32767).astype(np.int16)

    with wave.open(OUT, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


if __name__ == "__main__":
    main()
