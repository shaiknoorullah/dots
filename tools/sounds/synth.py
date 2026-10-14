#!/usr/bin/env python3
"""Render the dots notification sound (original synthesis, no samples).

Palette: B major pentatonic, soft sine bells with decaying 2nd/3rd partials, light Schroeder reverb.
`message`: B4 then F#5 90 ms later (a gentle rising fifth), 1.2 s, peak -10 dBFS.

Usage: python3 tools/sounds/synth.py   (needs ffmpeg with libopus; rewrites the .oga in the chezmoi source)
"""
import math, os, struct, subprocess, tempfile, wave

SR = 48000
OUT = os.path.join(os.path.dirname(__file__), '..', '..', 'home', 'private_dot_local', 'private_share', 'sounds', 'dots')
N = {'B4': 493.88, 'F#5': 739.99}


def bell(b, start, f, amp, decay, bright=0.25):
    """fundamental + decaying 2nd and slightly inharmonic 3rd partial, 5 ms attack"""
    s0 = int(start * SR)
    for i in range(int(decay * 6 * SR)):
        if s0 + i >= len(b):
            break
        t = i / SR
        v = math.sin(2 * math.pi * f * t)
        v += bright * math.sin(2 * math.pi * 2 * f * t) * math.exp(-t / (decay * 0.35))
        v += bright * 0.35 * math.sin(2 * math.pi * 3.01 * f * t) * math.exp(-t / (decay * 0.2))
        b[s0 + i] += amp * min(1.0, t / 0.005) * math.exp(-t / decay) * v


def reverb(b, mix=0.28):
    """Schroeder: 4 parallel combs, 2 series allpasses"""
    def comb(x, d, g):
        y = [0.0] * len(x)
        for i in range(len(x)):
            y[i] = x[i] + (g * y[i - d] if i >= d else 0.0)
        return y

    def allpass(x, d, g):
        y = [0.0] * len(x)
        for i in range(len(x)):
            xd = x[i - d] if i >= d else 0.0
            yd = y[i - d] if i >= d else 0.0
            y[i] = -g * x[i] + xd + g * yd
        return y

    wet = [0.0] * len(b)
    for d, g in ((1557, 0.84), (1617, 0.83), (1491, 0.85), (1422, 0.84)):
        c = comb(b, int(d * SR / 44100), g)
        for i in range(len(b)):
            wet[i] += c[i] / 4
    for d, g in ((225, 0.5), (556, 0.5)):
        wet = allpass(wet, int(d * SR / 44100), g)
    return [(1 - mix) * x + mix * w for x, w in zip(b, wet)]


def write_wav(path, b, peak_db):
    g = 10 ** (peak_db / 20) / max(1e-9, max(abs(v) for v in b))
    n = int(0.01 * SR)  # 10 ms fade-out guard
    for i in range(n):
        b[-n + i] *= 1 - i / n
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b''.join(struct.pack('<h', int(max(-1, min(1, v * g)) * 32767)) for v in b))


def message():
    b = [0.0] * int(SR * 1.2)
    bell(b, 0.00, N['B4'], 0.30, 0.35)
    bell(b, 0.09, N['F#5'], 0.24, 0.45)
    return reverb(b), -10


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for name, render in (('message', message),):
        b, peak = render()
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, name + '.wav')
            write_wav(wav, b, peak)
            oga = os.path.join(OUT, name + '.oga')
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', wav,
                            '-c:a', 'libopus', '-b:a', '64k', '-map_metadata', '-1', '-fflags', '+bitexact', oga],
                           check=True)
        print(os.path.relpath(oga))
