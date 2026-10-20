#!/usr/bin/env python3
"""Presenter camera track for the walkthrough: a square video, on the final timeline, of the speaker.

Lip sync: while a narration block plays, the bubble shows the camera frames from exactly the same moments of the
recording as the voice (same cut points as build_paced.py). Between blocks, when the screen is still moving but
nobody is talking, it fills the gap with genuine quiet moments from the recording (no speech nearby, no cut
headings), blended with short crossfades, so the presenter never freezes.

Usage: cam_bubble.py <cam.mkv (30 fps)> <words.json> <plan.json> <edit.json> <out.mp4> [size]
The output is size×size (default 320), 30 fps, exactly edit.total long, no audio. assemble.py masks it into a circle."""
import json, os, subprocess, sys
import numpy as np
import cv2

cam, words_p, plan_p, edit_p, out = sys.argv[1:6]
SIZE = int(sys.argv[6]) if len(sys.argv) > 6 else 320
FPS = 30
words, plan, edit = json.load(open(words_p)), json.load(open(plan_p)), json.load(open(edit_p))
tmp = out + ".frames.u8"


def probe_size(p):
    w, h = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                    "-of", "csv=p=0", p], text=True).strip().split(",")
    return int(w), int(h)


W, H = probe_size(cam)

# 1) one steady crop around the face (median of detections, sampled once a second)
casc = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", cam, "-vf", "fps=1,format=gray", "-f", "rawvideo", "-"])
faces = []
for i in range(len(raw) // (W * H)):
    g = np.frombuffer(raw, np.uint8, W * H, i * W * H).reshape(H, W)
    f = casc.detectMultiScale(g, 1.15, 6, minSize=(W // 12, W // 12))
    if len(f):
        x, y, w, h = max(f, key=lambda r: r[2] * r[3])
        faces.append((x + w / 2, y + h / 2, h))
assert faces, "no face found"
cx, cy, fh = (float(np.median([f[k] for f in faces])) for k in range(3))
side = int(min(H, W, fh * 2.0)) // 2 * 2
x0 = int(np.clip(cx - side / 2, 0, W - side))
y0 = int(np.clip(cy - side * 0.42, 0, H - side))          # face slightly above centre: head and shoulders
print(f"face in {len(faces)} samples; median face {fh:.0f}px; crop {side}px at ({x0},{y0})")

# 2) decode the cropped, lightly corrected camera once into a memory-mapped frame store
look = "hqdn3d=1.5:1.5:4:4,eq=brightness=0.035:contrast=1.07:saturation=1.08:gamma=1.04,unsharp=5:5:0.4"
p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", cam, "-vf", f"crop={side}:{side}:{x0}:{y0},{look},scale={SIZE}:{SIZE}:flags=lanczos,fps={FPS}",
                      "-f", "rawvideo", "-pix_fmt", "rgb24", tmp])
p.wait()
fsz = SIZE * SIZE * 3
N = os.path.getsize(tmp) // fsz
store = np.memmap(tmp, np.uint8, "r", shape=(N, SIZE, SIZE, 3))


def frame_at(t):
    return store[int(np.clip(round(t * FPS), 0, N - 1))]


# 3) what the bubble shows at each output frame
total = edit["total"]
T = int(round(total * FPS))
src = [None] * T  # each entry: list of (rec_time, weight)
cuts_all = [c for b in plan for c in b.get("cuts", [])]
speech = [(w[1], w[2]) for w in words]


def is_quiet(t, pad=0.12):
    return not any(a - pad <= t <= b + pad for a, b in speech) and not any(a - 0.1 <= t <= b + 0.1 for a, b in cuts_all)


# idle pool: stretches of the recording with no speech and no cut heading nearby
idle, run = [], None
for k in range(int(N)):
    t = k / FPS
    if is_quiet(t):
        run = run or [t, t]; run[1] = t
    elif run:
        if run[1] - run[0] >= 0.5: idle.append(tuple(run))
        run = None
if run and run[1] - run[0] >= 0.5: idle.append(tuple(run))
print(f"idle pool: {len(idle)} clips, {sum(b - a for a, b in idle):.1f}s")


def block_keep(p):
    keep, pos = [], p["a0"]
    for c0, c1 in p.get("cuts", []):
        keep.append((pos, c0)); pos = c1
    keep.append((pos, p["a1"]))
    return [(a, b) for a, b in keep if b > a]


XF = 0.2  # crossfade seconds at every join that isn't continuous in the recording
segments = []  # (out_start, out_end, rec_start)  rec time advances 1:1 with output time
for b, p in zip(edit["blocks"], plan):
    t = b["voice_at"]
    for a, z in block_keep(p):
        segments.append((t, t + (z - a), a)); t += z - a
segments.sort()

# fill every uncovered stretch with idle footage
covered = np.zeros(T, bool)
for s, e, r in segments:
    covered[int(s * FPS):min(T, int(np.ceil(e * FPS)))] = True
fills, k, ii = [], 0, 0
while k < T:
    if covered[k]:
        k += 1; continue
    j = k
    while j < T and not covered[j]: j += 1
    t, end = k / FPS, j / FPS
    # prefer the quiet moment right after the previous block's speech (natural continuation)
    prev = [sg for sg in segments if sg[1] <= t + 1e-6]
    if prev:
        r_after = prev[-1][2] + (prev[-1][1] - prev[-1][0])
        q_end = r_after
        while q_end < N / FPS and is_quiet(q_end, 0.15) and q_end - r_after < end - t: q_end += 1 / FPS
        cont = q_end - r_after
        if cont >= 0.3:
            if t + cont >= end - 1e-6:
                fills.append((t, end, r_after)); t = end
            else:  # overlap the next idle clip by XF so the switch is a crossfade, not a jump
                fills.append((t, t + cont, r_after)); t += cont - XF
    while t < end - 1e-6:
        a, z = idle[ii % len(idle)]; ii += 1
        d = min(z - a, end - t + XF)
        fills.append((t, t + d, a)); t += d - XF if t + d < end else d
    k = j
allsegs = sorted(segments + fills)

# 4) render: weighted blend where segments overlap (crossfades), straight copy elsewhere
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SIZE}x{SIZE}", "-r", str(FPS), "-i", "-",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
si = 0
for k in range(T):
    t = k / FPS
    act = [(s, e, r) for s, e, r in allsegs[max(0, si - 4):si + 6] if s - 1e-6 <= t < e]
    while si < len(allsegs) - 1 and allsegs[si][1] <= t: si += 1
    if not act:  # should not happen; hold the nearest frame
        s, e, r = min(allsegs, key=lambda g: abs(g[0] - t)); act = [(s, e, r)]
    if len(act) == 1:
        s, e, r = act[0]; img = frame_at(r + (t - s))
    else:
        acc, wsum = np.zeros((SIZE, SIZE, 3), np.float32), 0.0
        for s, e, r in act:
            w = min(1.0, (t - s) / XF + 1e-3, (e - t) / XF + 1e-3)
            acc += w * frame_at(r + (t - s)); wsum += w
        img = (acc / wsum).astype(np.uint8)
    enc.stdin.write(np.ascontiguousarray(img).tobytes())
enc.stdin.close(); enc.wait()
del store; os.remove(tmp)
print(f"{T} frames -> {out}; {len(segments)} speech segments, {len(fills)} fills")
