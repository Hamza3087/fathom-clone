#!/usr/bin/env python3
"""Sound pass: original music bed + ducked mix + loudness master for the Sounding walkthrough.

Usage:
    python3 sound.py [--in IN_DIR] [--out OUT_DIR] [--no-verify]

    IN_DIR  defaults to the shared polish input dir (base.voice.wav, base.edit.json).
    OUT_DIR defaults to IN_DIR/out/sound and receives:
        mix.wav        final master, 48 kHz / 24-bit stereo, exactly `total` seconds,
                       -16 LUFS integrated, true peak <= -1.5 dBTP, 0.6 s fade in / 1.1 s fade out
        music.wav      the music stem exactly as it sits in the mix (ducked, master gain, fades)
        report.txt     ffmpeg ebur128/astats measurements (loudness, true peak, speech-vs-music, gaps)
        spec_*.png     spectrograms used for the visual click/harshness check

Everything is synthesised here with numpy/scipy (no samples), so the music is original by construction.

Music: F major, 89.88 BPM (tempo chosen so bar 101 lands exactly on the end card at 269.7 s).
  pad    - three detuned PolyBLEP saws + a sine per note, 4-pole low-pass whose cutoff drifts slowly;
           each pitch is one continuous oscillator faded in/out with the chords (legato voice leading)
  piano  - felt-piano-like additive tone (inharmonic partials, two-stage decay, soft 7 ms attack)
           playing a sparse 3-4 note arpeggio per bar
  pulse  - soft sine bass on beats 1 and 3, plus a very quiet sustained sub under the pad
  space  - synthetic stereo convolution reverb (decorrelated decaying noise), then HPF 40 Hz,
           -3 dB dip at 2.8 kHz and a 6 kHz low-pass to keep out of the voice's way
  form   - Fmaj9 | Am7 | Dm9 | Bbmaj9 (2 bars each) loop; swell + filter opening into the title card-off;
           end card: Bbmaj9 -> C9sus4 -> Fmaj9 (IV-V-I), piano roll, ring-out into the video's fade.

Mix: voice gets only a +2 dB presence bell at 3 kHz (Q 0.7); music is ducked from a voice-activity envelope
(80 ms look-ahead, 100 ms attack, 350 ms hold, 650 ms release) so it sits ~17.5 dB under speech and rises
7.5 dB in pauses and under the cards.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
from scipy import signal
from scipy.io import wavfile
from scipy.ndimage import convolve1d, minimum_filter1d

SR = 48000
CR = 200  # control rate (Hz) for automation curves

DEFAULT_IN = "/tmp/claude-0/-root/a2db516c-9100-4760-bff1-2f9fe28ddb93/scratchpad/polish"

# mix settings
TARGET_LUFS = -16.0
TP_CEIL_DB = -1.8          # limiter ceiling (spec is <= -1.5 dBTP; leave margin for inter-sample error)
FREE_OFFSET_DB = -10.0     # unducked music loudness relative to the voice
DUCK_DB = 7.5              # extra attenuation while the voice is active
FADE_IN, FADE_OUT = 0.6, 1.1

# ---------------------------------------------------------------- music definition
# pad voicings (MIDI), bass note, arpeggio note set
CHORDS = {
    "F":    dict(pad=[53, 57, 60, 64, 67], bass=41, arp=[65, 72, 76, 79, 81]),
    "Am7":  dict(pad=[52, 57, 60, 64, 67], bass=45, arp=[64, 69, 72, 76, 79]),
    "Dm9":  dict(pad=[53, 57, 60, 64, 69], bass=38, arp=[62, 69, 72, 76, 77]),
    "Bb9":  dict(pad=[53, 57, 60, 62, 65], bass=46, arp=[65, 69, 72, 74, 77]),
    "Csus": dict(pad=[53, 55, 60, 62, 67], bass=48, arp=[67, 72, 74, 77, 79]),
}
CYCLE = ["F", "F", "Am7", "Am7", "Dm9", "Dm9", "Bb9", "Bb9"]
# (eighth-note position, arp index, velocity)
PAT_A = [(0, 0, .85), (3, 2, .60), (4, 1, .70), (6, 3, .55)]
PAT_B = [(0, 1, .80), (2, 3, .55), (4, 2, .65), (7, 4, .45)]
PAT_C = [(0, 0, .80), (3, 3, .60), (5, 2, .50)]
PAT_BREATH = [(0, 4, .45)]


def mtof(m):
    return 440.0 * 2 ** ((np.asarray(m, float) - 69) / 12)


def smoothstep(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def db(x):
    return 20 * np.log10(np.maximum(x, 1e-12))


# ---------------------------------------------------------------- loudness (BS.1770-4, 48 kHz)
_K1 = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585])
_K2 = ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])


def lufs(x):
    """Gated integrated loudness of x (samples,) or (samples, ch)."""
    x = x if x.ndim == 2 else x[:, None]
    y = signal.lfilter(*_K2, signal.lfilter(*_K1, x, axis=0), axis=0)
    blk, hop = int(0.4 * SR), int(0.1 * SR)
    n = 1 + (len(y) - blk) // hop
    if n < 1:
        return -np.inf
    c = np.cumsum(np.concatenate([np.zeros((1, y.shape[1])), y ** 2]), axis=0)
    idx = np.arange(n) * hop
    ms = ((c[idx + blk] - c[idx]) / blk).sum(1)
    lk = -0.691 + 10 * np.log10(np.maximum(ms, 1e-20))
    g = ms[lk > -70]
    if not len(g):
        return -np.inf
    rel = -0.691 + 10 * np.log10(g.mean()) - 10
    g = ms[(lk > -70) & (lk > rel)]
    return -0.691 + 10 * np.log10(g.mean())


def true_peak_db(x):
    x = x if x.ndim == 2 else x[:, None]
    return db(np.abs(signal.resample_poly(x, 4, 1, axis=0)).max())


# ---------------------------------------------------------------- synthesis helpers
def ctrl_to_audio(curve, n):
    tc = np.arange(len(curve)) / CR
    return np.interp(np.arange(n) / SR, tc, curve)


def smooth_noise(n_ctrl, rate_hz, rng):
    """Slow random curve in [-1, 1] at control rate."""
    k = max(2, int(n_ctrl / CR * rate_hz) + 2)
    pts = rng.uniform(-1, 1, k)
    return np.interp(np.linspace(0, k - 1, n_ctrl), np.arange(k), pts)


def polyblep_saw(freq, phase0=0.0):
    """Band-limited-ish saw from a per-sample frequency array (vectorised PolyBLEP)."""
    dt = freq / SR
    ph = (phase0 + np.cumsum(dt)) % 1.0
    y = 2 * ph - 1
    m = ph < dt
    t = ph[m] / dt[m]
    y[m] -= t + t - t * t - 1
    m = ph > 1 - dt
    t = (ph[m] - 1) / dt[m]
    y[m] -= t * t + t + t + 1
    return y


def rbj_lowpass_sos(fc, q):
    w = 2 * np.pi * fc / SR
    alpha = np.sin(w) / (2 * q)
    cw = np.cos(w)
    b0 = (1 - cw) / 2
    a0 = 1 + alpha
    return np.array([b0, 1 - cw, b0, a0, -2 * cw, 1 - alpha]) / a0


def tv_lowpass(x, fc_audio, block=256):
    """4-pole low-pass with a slowly varying cutoff: coefficients updated per block, filter state carried."""
    out = np.empty_like(x)
    zi = np.zeros((2, 2, x.shape[1]))
    for s in range(0, len(x), block):
        fc = float(fc_audio[min(s + block // 2, len(x) - 1)])
        sos = np.stack([rbj_lowpass_sos(fc, 0.541), rbj_lowpass_sos(fc, 1.307)])  # Butterworth Qs
        out[s:s + block], zi = signal.sosfilt(sos, x[s:s + block], axis=0, zi=zi)
    return out


def peaking_sos(f0, gain_db, q):
    a = 10 ** (gain_db / 40)
    w = 2 * np.pi * f0 / SR
    alpha = np.sin(w) / (2 * q)
    b = [1 + alpha * a, -2 * np.cos(w), 1 - alpha * a]
    aa = [1 + alpha / a, -2 * np.cos(w), 1 - alpha / a]
    return np.array([b + aa]) / aa[0]


def pan_gains(p):
    """Equal-power pan, p in [-1, 1]."""
    th = (p + 1) * np.pi / 4
    return np.cos(th), np.sin(th)


def piano_note(midi, vel, dur, rng):
    f = float(mtof(midi))
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    base_tau = 2.4 * (261.6 / f) ** 0.4
    bright = 2.3 - 0.8 * vel
    for k in range(1, 11):
        fk = k * f * np.sqrt(1 + 0.00025 * k * k)
        if fk > 6500:
            break
        tau = base_tau / (1 + 0.55 * (k - 1))
        env = 0.62 * np.exp(-t / (0.3 * tau)) + 0.38 * np.exp(-t / (1.7 * tau))
        ph = rng.uniform(0, 2 * np.pi)
        part = np.sin(2 * np.pi * fk * t + ph) + 0.45 * np.sin(2 * np.pi * fk * 1.00045 * t + ph + 1.0)
        y += k ** -bright * env * part
    att = int(0.007 * SR)
    y[:att] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(att) / att)
    rel = int(0.12 * SR)
    y[-rel:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(rel) / rel)
    return y * vel


def bass_note(midi, vel, dur, tau):
    f = float(mtof(midi))
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * f * t) + 0.28 * np.sin(4 * np.pi * f * t) + 0.08 * np.sin(6 * np.pi * f * t)
    env = np.exp(-t / tau)
    att = int(0.04 * SR)
    env[:att] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(att) / att)
    rel = int(0.15 * SR)
    env[-rel:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(rel) / rel)
    return y * env * vel


def add_at(buf, t0, mono, gl=1.0, gr=1.0):
    i = int(round(t0 * SR))
    if i >= len(buf):
        return
    seg = mono[: len(buf) - i]
    buf[i:i + len(seg), 0] += seg * gl
    buf[i:i + len(seg), 1] += seg * gr


def reverb_ir(rt60=2.6, predelay=0.025, seed=7):
    rng = np.random.default_rng(seed)
    n = int((rt60 + predelay) * SR)
    t = np.arange(n) / SR
    ir = np.zeros((n, 2))
    sos = signal.butter(1, 3800, fs=SR, output="sos")
    dark = signal.butter(1, 1500, fs=SR, output="sos")
    for ch in range(2):
        nz = rng.standard_normal(n)
        bright = signal.sosfilt(sos, nz)
        darker = signal.sosfilt(dark, nz)
        mix = np.clip(t / rt60, 0, 1)  # high frequencies die faster
        tail = (1 - mix) * bright + mix * darker
        env = np.exp(-6.91 * np.maximum(t - predelay, 0) / rt60) * (t >= predelay)
        fin = np.clip((t - predelay) / 0.012, 0, 1)
        ir[:, ch] = tail * env * fin
    return ir / np.sqrt((ir ** 2).sum(0))


def compose(total, cards):
    """Return (music stereo, layers dict for levels, bar length)."""
    n = int(round(total * SR))
    n_ctrl = int(np.ceil(total * CR)) + 2
    tc = np.arange(n_ctrl) / CR
    rng = np.random.default_rng(20260930)

    end_card = [c["t"] for c in cards if c["kind"] == "card"][-1]
    title_off = [c["t"] for c in cards if c["kind"] == "card-off"][0]
    n_body_bars = 101  # the IV chord of the final cadence starts exactly on the end card
    bar = end_card / n_body_bars
    beat = bar / 4
    n_bars = int(np.ceil(total / bar))

    def chord_of(b):
        if b >= 103:
            return "F"
        if b == 102:
            return "Csus"
        if b == 101:
            return "Bb9"
        return CYCLE[b % 8]

    # ---- per-pitch envelopes (pad + sustained sub), legato across chords
    ind_pad, ind_sub = {}, {}
    for b in range(n_bars):
        ch = CHORDS[chord_of(b)]
        a, z = int(b * bar * CR), min(int((b + 1) * bar * CR), n_ctrl)
        if b == n_bars - 1:
            z = n_ctrl
        for m in ch["pad"]:
            ind_pad.setdefault(m, np.zeros(n_ctrl))[a:z] = 1
        ind_sub.setdefault(ch["bass"], np.zeros(n_ctrl))[a:z] = 1
    win = np.hanning(int(0.9 * CR))
    win /= win.sum()
    shift = int(0.25 * CR)  # centre the crossfade just after the bar line

    def smooth(ind):
        s = convolve1d(ind, win, mode="nearest")
        return np.concatenate([np.full(shift, s[0]), s[:-shift]])

    intro = smoothstep(tc / 2.8)                                  # pad breathes in
    outro = 10 ** (-4 * smoothstep((tc - (end_card + 5.5)) / (total - end_card - 5.5)) / 20)
    # filter cutoff: opens into the title card-off, then a slow drift under the body, closes at the end
    drift = smooth_noise(n_ctrl, 1 / 9.0, rng)
    fc = 1050 * (1 + 0.18 * drift)
    fc = np.where(tc < title_off + 4, np.interp(tc, [0, title_off, title_off + 4], [380, 1750, 1050 * (1 + 0.18 * drift[int((title_off + 4) * CR)])]), fc)
    fc *= np.interp(tc, [0, end_card + 4, total], [1, 1, 0.62])

    # ---- pad
    pad = np.zeros((n, 2))
    detune = [(-9, -0.75), (0, 0.0), (9, 0.75)]
    for m, ind in sorted(ind_pad.items()):
        env = smooth(ind) * intro * outro
        nz = np.nonzero(env > 1e-4)[0]
        i0, i1 = int(nz[0] / CR * SR), min(n, int((nz[-1] + 1) / CR * SR) + 1)
        env_a = ctrl_to_audio(env, n)[i0:i1]
        f0 = float(mtof(m))
        voices = np.zeros((i1 - i0, 2))
        for cents, p in detune:
            wob = ctrl_to_audio(smooth_noise(n_ctrl, 0.12, rng) * 2.5, n)[i0:i1]  # +-2.5 cent drift
            fr = f0 * 2 ** ((cents + wob) / 1200)
            s = polyblep_saw(fr, rng.uniform())
            gl, gr = pan_gains(p)
            voices[:, 0] += s * gl
            voices[:, 1] += s * gr
        sine = np.sin(2 * np.pi * np.cumsum(np.full(i1 - i0, f0)) / SR + rng.uniform(0, 6.28))
        voices += 0.9 * sine[:, None]
        pad[i0:i1] += voices * env_a[:, None]
    pad = tv_lowpass(pad, ctrl_to_audio(fc, n))

    # ---- sustained sub (very quiet), follows the bass note
    sub = np.zeros(n)
    for m, ind in ind_sub.items():
        env = smooth(ind) * intro * outro * smoothstep((tc - 2 * bar) / (2 * bar))
        sub += np.sin(2 * np.pi * float(mtof(m)) * np.arange(n) / SR) * ctrl_to_audio(env, n)
    sub = np.stack([sub, sub], 1)

    # ---- piano arpeggio
    piano = np.zeros((n, 2))
    for b in range(1, n_bars):
        t_bar = b * bar
        name = chord_of(b)
        arp = CHORDS[name]["arp"]
        if b >= 103:
            break
        if b == 102:
            pat = [(0, 0, .70), (2, 1, .55), (4, 2, .55), (6, 4, .45)]
        elif b % 16 == 15:
            pat = PAT_BREATH
        elif b % 2 == 0:
            pat = PAT_A
        else:
            pat = PAT_B if (b // 4) % 2 == 0 else PAT_C
        vscale = 0.45 + 0.55 * smoothstep((t_bar - bar) / (title_off - bar))
        for pos, idx, v in pat:
            t0 = t_bar + pos * beat / 2 + rng.normal(0, 0.006)
            m = arp[idx]
            vv = v * vscale * rng.uniform(0.9, 1.05)
            gl, gr = pan_gains(np.clip((m - 71) / 14, -0.4, 0.4))
            add_at(piano, t0, piano_note(m, vv, 3.6, rng), gl, gr)
    # end: a soft rolled Fmaj9 on the final downbeat, then one high F
    t_end = 103 * bar
    for i, m in enumerate([53, 60, 64, 69, 72]):
        gl, gr = pan_gains((m - 63) / 20)
        add_at(piano, t_end + 0.085 * i, piano_note(m, 0.62 - 0.04 * i, min(5.5, total - t_end), rng), gl, gr)
    t_last = 104 * bar
    if t_last < total - 1.5:
        add_at(piano, t_last, piano_note(77, 0.36, total - t_last, rng), *pan_gains(0.2))

    # ---- soft low pulse on beats 1 and 3 (enters as the title card lifts)
    pulse = np.zeros((n, 2))
    for b in range(3, n_bars):
        bass = CHORDS[chord_of(b)]["bass"]
        ramp = smoothstep((b * bar - 3 * bar) / (title_off + bar - 3 * bar))
        if b >= 103:
            add_at(pulse, b * bar, bass_note(bass, 0.9, min(4.5, total - b * bar), 1.6))
            break
        add_at(pulse, b * bar, bass_note(bass, 1.0 * ramp, 1.25, 0.42))
        add_at(pulse, b * bar + 2 * beat, bass_note(bass, 0.62 * ramp, 1.25, 0.42))

    # ---- reverb sends and balance (levels set by loudness over the body so the balance is deterministic)
    body = slice(int((title_off + 4) * SR), int(end_card * SR))
    layer_rel = {"pad": 0.0, "piano": -3.5, "pulse": -11.0, "sub": -15.0}
    layers = {"pad": pad, "piano": piano, "pulse": pulse, "sub": sub}
    for k, x in layers.items():
        layers[k] = x * 10 ** ((layer_rel[k] - lufs(x[body])) / 20)
    ir = reverb_ir()
    send = 0.30 * layers["pad"] + 0.55 * layers["piano"] + 0.05 * layers["pulse"]
    wet = np.stack([signal.oaconvolve(send[:, c], ir[:, c])[:n] for c in range(2)], 1)
    music = layers["pad"] + layers["piano"] + layers["pulse"] + layers["sub"] + 0.9 * wet

    # music bus EQ: keep the lows tidy, dip the speech-intelligibility band, gentle high cut
    music = signal.sosfilt(signal.butter(2, 40, "high", fs=SR, output="sos"), music, axis=0)
    music = signal.sosfilt(peaking_sos(2800, -3.0, 0.8), music, axis=0)
    music = signal.sosfilt(signal.butter(2, 6000, fs=SR, output="sos"), music, axis=0)

    # subtle swell into the title card-off, relax under the body, a touch of lift under the end card
    swell = (np.interp(tc, [0, 3, title_off, title_off + 4.5], [0, 0, 2.0, 0]) +
             1.0 * smoothstep((tc - end_card) / 3.0))
    music *= ctrl_to_audio(10 ** (swell / 20), n)[:, None]
    return music, bar


# ---------------------------------------------------------------- voice + ducking
def polish_voice(v):
    # no high-pass: there is no rumble (<70 Hz sits ~50 dB down) and one would eat into his ~100 Hz fundamental
    return signal.sosfilt(peaking_sos(3000, 2.0, 0.7), v)


def duck_curve(v, n):
    """Music gain (linear, per sample) from voice activity: 0 dB in pauses, -DUCK_DB under speech."""
    hop, winl = SR // 100, int(0.03 * SR)
    nf = len(v) // hop
    c = np.concatenate([[0], np.cumsum(v ** 2)])
    starts = np.clip(np.arange(nf) * hop + hop // 2 - winl // 2, 0, len(v) - winl)
    lvl = db(np.sqrt((c[starts + winl] - c[starts]) / winl))
    act = lvl > -40
    # hold: bridge pauses shorter than 350 ms; look-ahead: start ducking 80 ms before speech
    hold, look = 35, 8
    act = ~minimum_filter1d((~act).astype(np.uint8), hold, mode="nearest").astype(bool) | act
    act = act | np.concatenate([act[look:], np.zeros(look, bool)])
    tgt = np.where(act, -DUCK_DB, 0.0)
    g = np.empty(nf)
    a_att, a_rel = np.exp(-1 / (0.10 * 100)), np.exp(-1 / (0.65 * 100))
    cur = tgt[0]
    for i in range(nf):
        k = a_att if tgt[i] < cur else a_rel
        cur = k * cur + (1 - k) * tgt[i]
        g[i] = cur
    w = np.hanning(7)
    g = convolve1d(g, w / w.sum(), mode="nearest")
    t_f = (np.arange(nf) * hop + hop / 2) / SR
    return 10 ** (np.interp(np.arange(n) / SR, t_f, g) / 20), act


# ---------------------------------------------------------------- master
def tp_limit(x, ceil_db):
    """Look-ahead true-peak limiter (4x oversampled detection, 60 ms smooth gain dips)."""
    thr = 10 ** (ceil_db / 20)
    up = np.abs(signal.resample_poly(x, 4, 1, axis=0)).max(1)
    pk = up[: 4 * len(x)].reshape(-1, 4).max(1)
    need = np.minimum(1.0, thr / np.maximum(pk, 1e-9))
    if need.min() >= 1.0:
        return x, np.ones(len(x))
    half = int(0.03 * SR)
    g = minimum_filter1d(need, 2 * half + 1)
    w = np.hanning(2 * half + 1)
    g = convolve1d(g, w / w.sum(), mode="nearest")
    return x * g[:, None], g


def fades(n):
    f = np.ones(n)
    a, b = int(FADE_IN * SR), int(FADE_OUT * SR)
    f[:a] = np.sin(0.5 * np.pi * np.arange(a) / a) ** 2
    f[-b:] = np.cos(0.5 * np.pi * np.arange(1, b + 1) / b) ** 2
    return f


def write24(path, x):
    with tempfile.NamedTemporaryFile(suffix=".wav", dir=os.path.dirname(path), delete=False) as tf:
        tmp = tf.name
    wavfile.write(tmp, SR, x.astype(np.float32))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-c:a", "pcm_s24le", path], check=True)
    os.remove(tmp)


# ---------------------------------------------------------------- verification
def ff(args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats"] + args, capture_output=True, text=True)
    return r.stderr


def ebur(path, ss=None, dur=None):
    pre = []
    if ss is not None:
        pre = ["-ss", f"{ss:.3f}", "-t", f"{dur:.3f}"]
    out = ff(pre + ["-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    s = out[out.rfind("Summary:"):]
    get = lambda k: float(re.search(k + r":\s+(-?[\d.]+|-inf)", s).group(1))
    return dict(I=get("I"), LRA=get("LRA"), TP=get("Peak"))


def astats(path, ss=None, dur=None):
    pre = []
    if ss is not None:
        pre = ["-ss", f"{ss:.3f}", "-t", f"{dur:.3f}"]
    out = ff(pre + ["-i", path, "-af", "astats=measure_perchannel=none", "-f", "null", "-"])
    s = out[out.rfind("Overall"):]
    get = lambda k: float(re.search(k + r":\s+(-?[\d.]+|-inf)", s).group(1))
    return dict(peak=get("Peak level dB"), rms=get("RMS level dB"), rms_peak=get("RMS peak dB"),
                nsamp=get("Number of samples"))


def verify(out_dir, voice_path, edit, act_frames, lines):
    mix, mus = os.path.join(out_dir, "mix.wav"), os.path.join(out_dir, "music.wav")
    total = edit["total"]
    L = lines.append
    L("== ffmpeg measurements ==")
    e = ebur(mix)
    L(f"mix.wav   integrated {e['I']:.1f} LUFS  LRA {e['LRA']:.1f} LU  true peak {e['TP']:.1f} dBTP")
    a = astats(mix)
    L(f"mix.wav   sample peak {a['peak']:.2f} dBFS  RMS {a['rms']:.1f} dBFS  samples/ch {int(a['nsamp']) // 2}")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels,bits_per_raw_sample,"
                            "codec_name,duration_ts", "-of", "compact", mix], capture_output=True, text=True).stdout.strip()
    L(f"mix.wav   {probe}")
    L(f"expected duration_ts {int(round(total * SR))} ({total} s)")
    em = ebur(mus)
    L(f"music.wav integrated {em['I']:.1f} LUFS  true peak {em['TP']:.1f} dBTP")
    L(f"clipping: sample peak {a['peak']:.2f} dBFS (< 0 dBFS => no clipped samples)")

    L("")
    L("== speech vs music inside speech spans (ffmpeg ebur128 integrated over the span, both stems as in the mix) ==")
    L("block  span(s)            voice LUFS  music LUFS  diff   | voice RMS  music RMS  diff (astats)")
    diffs = []
    for b in edit["blocks"]:
        if b["n"] not in (1, 3, 5, 7, 9, 11, 13, 15, 16, 18):
            continue
        s0, d = b["voice_at"] + 0.5, b["voice_dur"] - 1.0
        ev, emu = ebur(voice_path, s0, d), ebur(mus, s0, d)
        av, am = astats(voice_path, s0, d), astats(mus, s0, d)
        diffs.append(ev["I"] - emu["I"])
        L(f"{b['n']:>5}  {s0:7.2f}-{s0 + d:7.2f}   {ev['I']:8.1f}  {emu['I']:9.1f}  {ev['I'] - emu['I']:5.1f}  |"
          f" {av['rms']:8.1f}  {am['rms']:8.1f}  {av['rms'] - am['rms']:5.1f}")
    L(f"mean speech-minus-music: {np.mean(diffs):.1f} LU (min {min(diffs):.1f}, max {max(diffs):.1f})")

    L("")
    L("== music-only level in gaps / cards (voice inactive) ==")
    L("span(s)            music RMS          voice RMS")
    blocks = edit["blocks"]
    gaps = [(0.0, blocks[0]["voice_at"], "title card before speech")]
    for p, q in zip(blocks, blocks[1:]):
        g0, g1 = p["voice_at"] + p["voice_dur"], q["voice_at"]
        if g1 - g0 > 0.9:
            gaps.append((g0, g1, f"between blocks {p['n']}-{q['n']}"))
    last = blocks[-1]["voice_at"] + blocks[-1]["voice_dur"]
    gaps.append((last, total, "end card after speech (includes fade out)"))
    for g0, g1, lab in gaps[:6] + gaps[-3:]:
        am, av = astats(mus, g0, g1 - g0), astats(voice_path, g0, g1 - g0)
        L(f"{g0:7.2f}-{g1:7.2f}   {am['rms']:>10.1f} dBFS RMS   voice {av['rms']:.1f}   {lab}")
    body = ebur(mus, 20, 240)
    L(f"music body 20-260 s integrated: {body['I']:.1f} LUFS (mixture of ducked and free)")
    L(f"voice-active fraction (VAD after hold): {act_frames.mean() * 100:.0f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="indir", default=DEFAULT_IN)
    ap.add_argument("--out", dest="outdir", default=None)
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()
    out_dir = args.outdir or os.path.join(args.indir, "out", "sound")
    os.makedirs(out_dir, exist_ok=True)
    edit = json.load(open(os.path.join(args.indir, "base.edit.json")))
    total = float(edit["total"])
    n = int(round(total * SR))

    sr, v = wavfile.read(os.path.join(args.indir, "base.voice.wav"))
    assert sr == SR, sr
    v = v.astype(np.float64) / (32768.0 if v.dtype == np.int16 else 1.0)
    if v.ndim == 2:
        v = v.mean(1)
    v = np.pad(v, (0, max(0, n - len(v))))[:n]
    print(f"voice: {len(v) / SR:.3f} s, {lufs(v):.1f} LUFS")

    print("composing music ...", flush=True)
    music, bar = compose(total, edit["cards"])
    print(f"bar {bar:.4f} s = {240 / bar:.2f} BPM")

    vp = polish_voice(v)
    voice = np.stack([vp, vp], 1) * 10 ** (-3.01 / 20)  # centre-panned (dual mono at -3 dB)
    lv = lufs(voice)
    tc = np.arange(len(music)) / SR
    cards = edit["cards"]
    body = slice(int((cards[1]["t"] + 4) * SR), int(cards[-1]["t"] * SR))
    music *= 10 ** ((lv + FREE_OFFSET_DB - lufs(music[body])) / 20)
    duck, act = duck_curve(vp, n)
    music *= duck[:, None]

    fd = fades(n)[:, None]
    mix = (voice + music) * fd
    g = 10 ** ((TARGET_LUFS - lufs(mix)) / 20)
    mix, voice, music = mix * g, voice * g * fd, music * g * fd
    for it in range(4):
        mix_l, gl = tp_limit(mix, TP_CEIL_DB - 0.1 * it)
        tp = true_peak_db(mix_l)
        if tp <= TP_CEIL_DB + 0.05:
            break
    print(f"limiter: max gain reduction {-db(gl.min()):.2f} dB, true peak {tp:.2f} dBTP, LUFS {lufs(mix_l):.2f}")
    mix, voice, music = mix_l, voice * gl[:, None], music * gl[:, None]
    # loudness can drop a hair after limiting; nudge back if it stays inside the TP budget
    corr = TARGET_LUFS - lufs(mix)
    if abs(corr) > 0.05 and true_peak_db(mix) + corr <= TP_CEIL_DB:
        mix, voice, music = (x * 10 ** (corr / 20) for x in (mix, voice, music))

    write24(os.path.join(out_dir, "mix.wav"), mix)
    write24(os.path.join(out_dir, "music.wav"), music)
    tmpd = tempfile.mkdtemp(prefix="sound_", dir=os.path.dirname(os.path.dirname(out_dir)))
    voice_path = os.path.join(tmpd, "voice_stem.wav")
    write24(voice_path, voice)

    lines = ["Sounding walkthrough - sound pass report", "",
             f"script: {os.path.abspath(__file__)}", f"input: {args.indir}", "",
             "== internal (numpy BS.1770) ==",
             f"voice as delivered {lufs(v):.2f} LUFS (mono); in mix {lufs(voice):.2f} LUFS",
             f"mix {lufs(mix):.2f} LUFS, true peak {true_peak_db(mix):.2f} dBTP, "
             f"limiter max GR {-db(gl.min()):.2f} dB ({(gl < 0.999).mean() * 100:.2f}% of samples touched)",
             f"music free level target = voice {FREE_OFFSET_DB:+.1f} LU, duck {DUCK_DB} dB",
             f"tempo {240 / bar:.3f} BPM (bar {bar:.4f} s)",
             f"L/R correlation of music stem: {np.corrcoef(music[:, 0], music[:, 1])[0, 1]:.3f}", ""]
    if not args.no_verify:
        verify(out_dir, voice_path, edit, act, lines)
        mixp = os.path.join(out_dir, "mix.wav")
        for name, ss, d, src in [("spec_mix_0-30.png", 0, 30, mixp), ("spec_mix_120-150.png", 120, 30, mixp),
                                 ("spec_music_0-30.png", 0, 30, os.path.join(out_dir, "music.wav")),
                                 ("spec_mix_255-282.png", 255, total - 255, mixp)]:
            ff(["-y", "-ss", str(ss), "-t", f"{d:.3f}", "-i", src, "-lavfi",
                "showspectrumpic=s=1600x600:mode=combined:scale=log:fscale=log:legend=1:stop=16000",
                os.path.join(out_dir, name)])
        lines.append("")
        lines.append("spectrograms: " + ", ".join(p for p in sorted(os.listdir(out_dir)) if p.endswith(".png")))
    os.remove(voice_path)
    os.rmdir(tmpd)
    open(os.path.join(out_dir, "report.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
