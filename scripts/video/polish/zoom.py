#!/usr/bin/env python3
"""Camera pass: smooth, Screen-Studio-style auto-zoom for the Sounding walkthrough.

Reads the video stream of IN/base.mp4 and the focus/cards/fast timeline of
IN/base.edit.json, designs a calm camera path (per frame: crop window x0, y0, w
with h = w * 9/16, zoom s = 1920 / w) and renders it with sub-pixel accuracy.

Usage:
    python3 zoom.py                 # plan + render IN/out/zoom/zoomed.mp4 + zoom_path.json
    python3 zoom.py --plan          # plan only: write zoom_path.json, print the zooms
    python3 zoom.py --checks        # after rendering: export check stills to IN/out/zoom/checks/
    python3 zoom.py --preview       # plan only + cropped stills of every zoom in IN/out/zoom/preview/
    IN=/path/to/polish python3 zoom.py   # override the input directory

How the path is built
  0. analyse(): one pass over a 960x540 proxy of base.mp4 (cached in OUT/analysis.npz)
     finds, per focus event, when the orange ring is really drawn on the rect, when it
     disappears or its element turns blank (both count as a clear, like a recorded
     `rect: null`), and "ink" masks (text/icons/buttons) sampled through the shot.
  1. Every focus event becomes a "shot" lasting until the next event or clear. Events
     shorter than MERGE_S (and clears shorter than CLEAR_S) are absorbed into the
     previous shot, so rapid ring moves do not make the camera twitch. A clear up to
     BRIDGE_S is also absorbed when the next target is already cleanly in view.
  2. A shot zooms only if its ring is visible, it is not a menu/navigation click
     (CLICK_THROUGH tags, any tag ending in "menu", untagged rings), it lasts >= MIN_SHOT_S, and a clean
     window exists: frame_window() searches scale (<= PREF_ZOOM, and no more than the
     region needs) and position for a window whose edges cross no ink in any snapshot,
     i.e. never cuts a text line, button or list row, so rows and cards are shown whole.
     Otherwise the shot stays wide.
     Title card, end card and fast-forward ranges are forced wide; the camera starts
     easing out SETTLE_S before them and stays at 1.0 for POST_BLOCK_S after them.
  3. Consecutive zoom shots form a chain. A chain shorter than MIN_CHAIN_S is dropped
     (a zoom in and straight out again feels busy). Inside a chain the camera keeps
     its window if the next target is already cleanly in view, frames several targets
     together when that costs little scale, and otherwise pans at the same scale.
  4. The region (rect + ring + label) sits in the upper SAFE_FRAC of the output frame
     (subtitles cover the bottom ~16%) and, while zoomed, never under the presenter
     camera bubble (BUBBLE, bottom-right, composited later); the window is clamped
     inside 1920x1080.
  5. The piecewise-constant target (x0, y0, w) goes through a short box pre-filter and a
     critically damped spring (no overshoot, continuous velocity). Interpolating the
     window rectangle linearly keeps every intermediate window inside the frame.

Rendering: ffmpeg decodes yuv420p frames to a pipe; each plane is resampled with
OpenCV (Lanczos-4, float affine, so slow pans have no 1-px stair-stepping) and piped
into libx264 (crf 17, preset slow). Frames at s = 1 are passed through untouched.
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

IN = os.environ.get("IN", "/tmp/claude-0/-root/a2db516c-9100-4760-bff1-2f9fe28ddb93/scratchpad/polish")
OUT = os.path.join(IN, "out", "zoom")
W, H, FPS = 1920, 1080, 30

# --- path parameters -------------------------------------------------------
MAX_ZOOM = 1.8        # hard cap
PREF_ZOOM = 1.4       # soft cap: a zoom never goes tighter than this (context stays)
MIN_ZOOM = 1.2        # below this a zoom is not worth the motion
FILL_W = 0.85         # never zoom more than needed: region <= 85% of the window width
FILL_H = 0.85         # ... and of the safe (subtitle-free) window height
SAFE_FRAC = 0.78      # region must stay in the upper 78% of the output frame
REGION_Y = 0.40       # ideal: region centre at 40% of the window height
EDGE_MARGIN = 40      # min output px between the region and the window edge
BUBBLE = (1590, 750, 1900, 1065)  # presenter bubble + ring + shadow (output px x0, y0, x1, y1)
BUBBLE_MARGIN = 16    # min output px between a zoomed region and the bubble
SIDEBAR_X = 226       # app's left nav (+ border): a left edge inside it slices the light nav pills,
                      # which the ink mask does not see, so the window shows all of it or none
BOTTOM_WT = 0.25      # weight of the bottom edge (under the subtitles)
EDGE_BAND = 4         # ... within +-4 half-res px (8 source px) of the edge
EDGE_TOL = 3          # window edges may touch at most this many ink px (half-res) per snapshot
PW, PH = 960, 540     # analysis proxy size
SNAP_EVERY_S = 0.7    # ink snapshot interval during a shot
MERGE_S = 0.8         # events shorter than this are absorbed into the previous shot
CLEAR_S = 1.5         # ring cleared for longer than this -> back to 1.0
VANISH_CLEAR_S = 1.2  # ... or this long after the ring / target itself disappeared
BRIDGE_S = 2.5        # longer clears still hold if the next target is already in view
RING_WAIT_S = 1.0     # the ring must be visible on the rect within this long to zoom
MIN_SHOT_S = 1.2      # a single target must be held this long to be zoomed
MIN_CHAIN_S = 2.6     # an isolated zoom (in -> out) must last at least this long
SETTLE_S = 1.5        # start easing out this long before a card / fast range
POST_BLOCK_S = 1.2    # stay at 1.0 this long after a card / fast range ends
LEAD_S = 0.15         # start moving slightly before the ring appears
BOX_S = 0.30          # pre-filter length (softens the spring's initial kick)
OMEGA = 5.0           # spring stiffness (rad/s); ~0.95 s to 95% of a move
UNION_KEEP = 0.85     # frame two targets together if the scale drops by < 15%
HOLD_DS = 0.25        # keep the current window if the new target fits and ds < this


def load_edit():
    with open(os.path.join(IN, "base.edit.json")) as f:
        return json.load(f)


def probe_frames():
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
        os.path.join(IN, "base.mp4")]).decode().strip()
    return int(out.split(",")[0])


# Menu / navigation clicks: the page changes under the ring right after the click,
# so a zoom would frame a stale ring. These shots stay wide (the camera eases out).
# Any tag ending in "menu" (Line menu, Clip menu, ...) counts too, and so does an untagged
# ring: in this recording those mark a click on a row / input that navigates or opens a modal.
CLICK_THROUGH = {"No sign-up, no login", "Organization settings", "Upload recording", "Upload and transcribe",
                 "Clip from here", "End clip here", "Save clip", "Copy public link", "Share", "Jump to 16:00"}


def is_click_through(tag):
    return not tag.strip() or tag in CLICK_THROUGH or tag.strip().lower().endswith("menu")


def region_of(rect, tag):
    """Rect + ring padding + label box above it, in source pixels."""
    x, y, w, h = rect["x"], rect["y"], rect["width"], rect["height"]
    label_w = 10.0 * len(tag) + 24 if tag else 0.0
    x0 = x - 12
    x1 = max(x + w + 12, x - 8 + label_w)
    if tag and y - 46 < 0:  # at the page top the label is drawn below the ring
        y0, y1 = y - 12, y + h + 46
    else:
        y0, y1 = y - (46 if tag else 12), y + h + 12
    if x0 < -20 or y0 < -60 or x1 > W + 160 or y1 > H + 20:
        return None  # target partly off-page (e.g. a whole scrolled chart)
    # Rings and labels at the page edge are drawn clipped / nudged inside the page.
    return [max(x0, 0.0), max(y0, 0.0), min(x1, float(W)), min(y1, float(H))]


def union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def under_bubble(reg, win):
    """True if region reg, seen through a zoomed window, lands on the presenter bubble."""
    x0, y0, w = win
    s = W / w
    if s < 1.001:
        return False  # at 1.0 nothing can be done
    m = BUBBLE_MARGIN
    return ((reg[2] - x0) * s > BUBBLE[0] - m and (reg[0] - x0) * s < BUBBLE[2] + m and
            (reg[3] - y0) * s > BUBBLE[1] - m and (reg[1] - y0) * s < BUBBLE[3] + m)


def fits_in(reg, win, margin=24):
    x0, y0, w = win
    h = w * H / W
    m = margin * w / W
    return (reg[0] >= x0 + m and reg[2] <= x0 + w - m and
            reg[1] >= y0 + m and reg[3] <= y0 + SAFE_FRAC * h - m and not under_bubble(reg, win))


WIDE = (0.0, 0.0, float(W))


def analyse(edit, n):
    """One streaming pass over a 960x540 proxy of base.mp4 (~1 min, cached in OUT).

    For every focus event i (rect, until the next event) it measures:
      ring_on[i]   first time the orange ring is actually drawn around the rect (None: never)
      clear[i]     time the ring disappears (>= 0.4 s), or the ringed element turns blank
                   while the ring stays (menu closed, modal submitted, page navigated)
      ink[i]       "ink" masks (text, icons, buttons; not light borders or card fills) from
                   0.8 s in and then every SNAP_EVERY_S, used to place zoom windows that
                   never cut a line
    """
    base = os.path.join(IN, "base.mp4")
    key = f"v3-{SNAP_EVERY_S}-{os.path.getsize(base)}-{int(os.path.getmtime(base))}-" + \
        str(abs(hash(json.dumps(edit["focus"], sort_keys=True))) % 10**12)
    cache = os.path.join(OUT, "analysis.npz")
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        if str(z["key"]) == key:
            return z["ring_on"].item(), z["clear"].item(), {
                i: [np.unpackbits(m, axis=None)[:PH * PW].reshape(PH, PW).astype(bool) for m in ms]
                for i, ms in z["ink"].item().items()}

    ev = sorted(edit["focus"], key=lambda e: e["t"])
    ends = [e["t"] for e in ev[1:]] + [n / FPS]
    boxes = {}
    for i, e in enumerate(ev):
        r = e["rect"]
        if r and r["width"] < 1000 and r["y"] >= 0:
            boxes[i] = [r["x"] / 2, r["y"] / 2, (r["x"] + r["width"]) / 2, (r["y"] + r["height"]) / 2]
    vis = {i: [] for i in boxes}
    tex = {i: [] for i in boxes}
    ink = {i: [] for i in boxes}
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", base, "-vf", f"scale={PW}:{PH}:flags=area",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    fsz = PW * PH * 3
    k = 0
    while True:
        buf = dec.stdout.read(fsz)
        if len(buf) < fsz:
            break
        t = k / FPS
        k += 1
        act = [i for i in boxes if ev[i]["t"] <= t < ends[i]]
        if not act:
            continue
        f = np.frombuffer(buf, np.uint8).reshape(PH, PW, 3).astype(np.int16)
        grey = None
        for i in act:
            x0, y0, x1, y1 = (int(v) for v in boxes[i])
            # Ring: orange pixels in the bands left, right and below the rect (the label is above).
            a0, a1, b0, b1 = max(x0 - 9, 0), min(x1 + 9, PW), max(y0 - 2, 0), min(y1 + 9, PH)
            sub = f[b0:b1, a0:a1]
            m = ((sub[..., 0] > 200) & (sub[..., 1] > 70) & (sub[..., 1] < 180) & (sub[..., 2] < 110)
                 & (sub[..., 0] - sub[..., 1] > 60))
            m[:max(y1 - b0 - 2, 0), max(x0 - a0 + 2, 0):max(x1 - a0 - 2, 0)] = False
            vis[i].append(m.sum() / max(1, 2 * (y1 - y0) + (x1 - x0)))
            # Texture inside the rect on a quarter-res grid (blank = element gone).
            if grey is None:
                grey = f @ np.array([299, 587, 114]) // 1000
            q = grey[y0 + 2:y1 - 2, x0 + 2:x1 - 2]
            q = q[:q.shape[0] // 2 * 2, :q.shape[1] // 2 * 2]
            if q.size >= 4:
                q = q.reshape(q.shape[0] // 2, 2, q.shape[1] // 2, 2).mean((1, 3))
                tex[i].append((np.abs(q - np.median(q)) > 12).mean())
            else:
                tex[i].append(1.0)
            # Snapshots 0.8 s in, then every SNAP_EVERY_S (content can reload or scroll).
            if t >= ev[i]["t"] + 0.8 + SNAP_EVERY_S * len(ink[i]) and t < ends[i] - 0.2:
                g = grey < 214  # text, icons, buttons; not the page (244), cards (255) or borders
                g[:2] = g[-2:] = False
                g[:, :2] = g[:, -2:] = False
                ink[i].append(g)
    dec.wait()

    ring_on, clear = {}, {}
    for i in boxes:
        v = np.array(vis[i]) > 0.5
        t0 = ev[i]["t"]
        on = np.flatnonzero(v)
        ring_on[i] = t0 + on[0] / FPS if len(on) else None
        cands = []
        if len(on):
            for j in range(on[0], len(v) - 12):
                if not v[j:j + 12].any():  # ring gone for 0.4 s
                    cands.append(t0 + j / FPS + 0.1)
                    break
        tx = np.array(tex[i])
        if len(tx) >= 10 and tx[:10].max() >= 0.15:
            blank = tx < 0.08
            for j in range(len(tx) - 15):
                if blank[j:j + 15].all():  # element blank for 0.5 s
                    cands.append(t0 + j / FPS + 0.25)
                    break
        if cands:
            clear[i] = min(cands)
    os.makedirs(OUT, exist_ok=True)
    np.savez_compressed(cache, key=key, ring_on=ring_on, clear=clear,
                        ink={i: [np.packbits(m, axis=None) for m in ms] for i, ms in ink.items()})
    return ring_on, clear, ink


def edge_cost(win, masks):
    """Ink pixels (half-res) the window edges would slice through: a cut text line,
    button or clip title. The bottom edge counts less (subtitles sit over it)."""
    x0, y0, w = win
    h = w * H / W
    cost = 0.0
    for m in masks:
        X0, X1 = int(round(x0 / 2)), int(round((x0 + w) / 2))
        Y0, Y1 = int(round(y0 / 2)), int(round((y0 + h) / 2))
        b = EDGE_BAND
        if X0 > 0:
            cost += m[Y0:Y1, max(X0 - b, 0):X0 + b].sum()
        if X1 < PW:
            cost += m[Y0:Y1, X1 - b:X1 + b].sum()
        if Y0 > 0:
            cost += m[max(Y0 - b, 0):Y0 + b, X0:X1].sum()
        if Y1 < PH:
            cost += BOTTOM_WT * m[Y1 - b:Y1 + b, X0:X1].sum()
    return cost


def frame_window(reg, masks, s_cap=None):
    """Best crop window for region reg: the largest scale <= PREF_ZOOM (and no more than
    the region needs) whose edges cut no content, placed as close as possible to the ideal
    framing (region centred, at REGION_Y of the height, inside the subtitle-free area).
    The region must also stay clear of the presenter bubble (BUBBLE) in the output.
    Returns (x0, y0, w) or None if no zoom >= MIN_ZOOM frames it cleanly."""
    if reg is None:
        return None
    rw, rh = reg[2] - reg[0], reg[3] - reg[1]
    s_max = min(PREF_ZOOM, W * FILL_W / rw, H * SAFE_FRAC * FILL_H / rh, s_cap or MAX_ZOOM)
    if not masks:
        return None
    cols = [np.vstack([np.zeros((1, PW)), np.cumsum(m, 0)]) for m in masks]
    rows = [np.hstack([np.zeros((PH, 1)), np.cumsum(m, 1)]) for m in masks]
    s = s_max
    while s >= MIN_ZOOM - 1e-9:
        w, h = W / s, H / s
        mg = EDGE_MARGIN / s
        xa, xb = max(0.0, reg[2] + mg - w), min(reg[0] - mg, W - w)
        ya, yb = max(0.0, reg[3] + mg - SAFE_FRAC * h), min(reg[1] - mg, H - h)
        if xa <= xb + 1e-6 and ya <= yb + 1e-6:
            xs = np.unique(np.clip(np.round(np.linspace(xa, xb, max(2, int((xb - xa) / 4) + 1)) / 2), 0, PW))
            ys = np.unique(np.clip(np.round(np.linspace(ya, yb, max(2, int((yb - ya) / 4) + 1)) / 2), 0, PH))
            X, Y = np.meshgrid(xs.astype(int), ys.astype(int))
            X1 = np.minimum(X + int(round(w / 2)), PW)
            Y1 = np.minimum(Y + int(round(h / 2)), PH)
            cost = np.zeros(X.shape)
            for c, r in zip(cols, rows):
                for xe in (X, X1):  # vertical edges: ink within EDGE_BAND columns, over [Y, Y1)
                    for dx in range(-EDGE_BAND, EDGE_BAND):
                        xc = np.clip(xe + dx, 0, PW - 1)
                        inside = (xe > 0) & (xe < PW)
                        cost += inside * (c[Y1, xc] - c[Y, xc])
                for ye, wt in ((Y, 1.0), (Y1, BOTTOM_WT)):  # horizontal edges
                    for dy in range(-EDGE_BAND, EDGE_BAND):
                        yc = np.clip(ye + dy, 0, PH - 1)
                        inside = (ye > 0) & (ye < PH)
                        cost += wt * inside * (r[yc, X1] - r[yc, X])
            ok = cost <= EDGE_TOL * len(masks)
            ok &= (X == 0) | (X * 2 >= SIDEBAR_X)
            # Bubble keep-out: the region's output rect must stay clear of BUBBLE.
            m = BUBBLE_MARGIN
            ok &= ~(((reg[2] - X * 2) * s > BUBBLE[0] - m) & ((reg[0] - X * 2) * s < BUBBLE[2] + m) &
                    ((reg[3] - Y * 2) * s > BUBBLE[1] - m) & ((reg[1] - Y * 2) * s < BUBBLE[3] + m))
            if ok.any():
                cx, cy = (reg[0] + reg[2]) / 2, (reg[1] + reg[3]) / 2
                ideal_x = min(max(cx - w / 2, 0.0), W - w)
                ideal_y = min(max(cy - REGION_Y * h, 0.0), H - h)
                d = np.abs(X * 2 - ideal_x) + np.abs(Y * 2 - ideal_y) + 50 * cost
                d[~ok] = np.inf
                j = np.unravel_index(np.argmin(d), d.shape)
                x0 = float(min(max(X[j] * 2, 0), W - w))
                y0 = float(min(max(Y[j] * 2, 0), H - h))
                return (x0, y0, w)
        s -= 0.025
    return None


def plan(edit, total, ring_on, clear, ink):
    """Return (keyframes, zooms, blocked). keyframes: [(t, (x0, y0, w))], a piecewise-constant target."""
    # Blocked intervals: title card, fast-forwards, end card.
    cards = sorted(edit["cards"], key=lambda c: c["t"])
    blocked = []
    t_on = None
    for c in cards:
        if c["kind"] == "card":
            t_on = 0.0 if c["t"] < 0.1 else c["t"]
        elif c["kind"] == "card-off" and t_on is not None:
            blocked.append((t_on, c["t"]))
            t_on = None
    if t_on is not None:
        blocked.append((t_on, total))
    blocked += [tuple(f) for f in edit["fast"]]
    blocked.sort()

    # 1. Raw shots from focus events. A detected clear (ring gone / element blank) ends
    #    the shot early and behaves like a recorded `rect: null` event.
    ev = sorted(edit["focus"], key=lambda e: e["t"])
    shots = []
    for i, e in enumerate(ev):
        t1 = ev[i + 1]["t"] if i + 1 < len(ev) else total
        tc = clear.get(i)
        tc = tc if tc is not None and tc < t1 else None
        shots.append({"i": i, "t0": e["t"], "t1": tc or t1, "rect": e["rect"], "tag": e["tag"] or ""})
        if tc:
            shots.append({"i": None, "t0": tc, "t1": t1, "rect": None, "tag": "", "vanish": True})

    def masks(sh):
        return ink.get(sh["i"], []) if sh["i"] is not None else []

    # Absorb short events / short ring-cleared gaps into the previous shot. A gap up to
    # BRIDGE_S long is also absorbed when the next target is already inside the window
    # of the previous one (zooming out and straight back in would feel busy).
    merged = []
    for i, sh in enumerate(shots):
        dur = sh["t1"] - sh["t0"]
        if sh["rect"] is None:
            short = dur < (VANISH_CLEAR_S if sh.get("vanish") else CLEAR_S)
            nxt = shots[i + 1] if i + 1 < len(shots) else None
            # Target gone and a menu/navigation click comes next: nothing left to frame.
            j = i + 1
            while j < len(shots) and shots[j]["rect"] is None:
                j += 1
            if sh.get("vanish") and j < len(shots) and is_click_through(shots[j]["tag"]):
                short = False
            if not short and dur < BRIDGE_S and merged and merged[-1]["rect"] and nxt and nxt["rect"] \
                    and not is_click_through(nxt["tag"]) and not is_click_through(merged[-1]["tag"]):
                a = region_of(merged[-1]["rect"], merged[-1]["tag"])
                b = region_of(nxt["rect"], nxt["tag"])
                win = frame_window(a, masks(merged[-1])) if a and b else None
                if win and fits_in(b, win) and edge_cost(win, masks(nxt)) <= EDGE_TOL * len(masks(nxt)):
                    short = True
        else:
            short = dur < MERGE_S
        if merged and short:
            merged[-1]["t1"] = sh["t1"]
        else:
            merged.append(dict(sh))

    # 2. Cut shots around blocked intervals: ease out SETTLE_S before, and stay wide for
    #    POST_BLOCK_S after (a fast-forward cut followed by a zoom reads as a jump).
    cut = []
    for sh in merged:
        pieces = [(sh["t0"], sh["t1"])]
        for b0, b1 in blocked:
            nxt = []
            for a0, a1 in pieces:
                lo, hi = b0 - SETTLE_S, b1 + POST_BLOCK_S
                if a1 <= lo or a0 >= hi:
                    nxt.append((a0, a1))
                    continue
                if a0 < lo:
                    nxt.append((a0, lo))
                if a1 > hi:
                    nxt.append((hi, a1))
            pieces = nxt
        for a0, a1 in pieces:
            cut.append(dict(sh, t0=a0, t1=a1))

    # A shot zooms only if its ring is really on screen, it is not a click-through, it
    # lasts long enough, and a window >= MIN_ZOOM frames it without cutting content.
    for sh in cut:
        sh["zoom"] = False
        if sh["rect"] is None or is_click_through(sh["tag"]) or sh["t1"] - sh["t0"] < MIN_SHOT_S:
            continue
        on = ring_on.get(sh["i"])
        if on is None or on > ev[sh["i"]]["t"] + RING_WAIT_S:
            continue
        reg = region_of(sh["rect"], sh["tag"])
        win = frame_window(reg, masks(sh))
        if win is None:
            continue
        sh.update(zoom=True, reg=reg, win=win, s=W / win[2], masks=masks(sh))

    # 3. Chains of consecutive (time-contiguous) zoom shots.
    chains, cur = [], []
    for sh in cut:
        if sh["zoom"] and cur and abs(sh["t0"] - cur[-1]["t1"]) < 1e-6:
            cur.append(sh)
        else:
            if cur:
                chains.append(cur)
            cur = [sh] if sh["zoom"] else []
    if cur:
        chains.append(cur)
    chains = [c for c in chains if c[-1]["t1"] - c[0]["t0"] >= MIN_CHAIN_S]

    keys = [(0.0, WIDE)]
    zooms = []
    for chain in chains:
        # Group targets that can share one clean window without losing much scale.
        groups = []
        for sh in chain:
            if groups:
                g = groups[-1]
                u = union(g["reg"], sh["reg"])
                wu = frame_window(u, g["masks"] + sh["masks"])
                if wu and W / wu[2] >= UNION_KEEP * min(g["s"], sh["s"]):
                    g.update(reg=u, win=wu, s=W / wu[2], t1=sh["t1"], tags=g["tags"] + [sh["tag"]],
                             masks=g["masks"] + sh["masks"])
                    continue
            groups.append({"t0": sh["t0"], "t1": sh["t1"], "reg": list(sh["reg"]), "win": sh["win"],
                           "s": sh["s"], "tags": [sh["tag"]], "masks": list(sh["masks"])})
        win = None
        for g in groups:
            # Hold the current window if the next target is already cleanly in view.
            if win is not None and fits_in(g["reg"], win) and \
                    edge_cost(win, g["masks"]) <= EDGE_TOL * len(g["masks"]):
                zooms[-1]["tags"] += g["tags"]
                zooms[-1]["t1"] = g["t1"]
                continue
            nw = g["win"]
            # Pan at a constant scale inside a chain unless the difference is large.
            if win is not None and abs(W / nw[2] - W / win[2]) < HOLD_DS:
                alt = frame_window(g["reg"], g["masks"], s_cap=min(W / win[2], W / nw[2]))
                nw = alt or nw
            win = nw
            tk = max(0.0, g["t0"] - LEAD_S)
            for b0, b1 in blocked:
                if b0 <= tk < b1 + POST_BLOCK_S:
                    tk = b1 + POST_BLOCK_S  # the lead must not eat into the post-range hold
            keys.append((tk, win))
            zooms.append({"t0": round(g["t0"], 3), "t1": round(g["t1"], 3), "s": round(W / win[2], 3),
                          "window": [round(v, 1) for v in win], "tags": list(g["tags"]),
                          "pan": len(zooms) > 0 and zooms[-1]["t1"] == g["t0"]})
        keys.append((chain[-1]["t1"] - LEAD_S, WIDE))

    keys.sort(key=lambda k: k[0])
    return keys, zooms, blocked


def camera_path(keys, blocked, n):
    """Per-frame (x0, y0, w): box pre-filter + critically damped spring, exact discretisation."""
    t = np.arange(n) / FPS
    tgt = np.zeros((n, 3))
    ki = 0
    for i in range(n):
        while ki + 1 < len(keys) and keys[ki + 1][0] <= t[i] + 1e-9:
            ki += 1
        tgt[i] = keys[ki][1]
    k = max(1, int(round(BOX_S * FPS)))
    pad = np.concatenate([np.repeat(tgt[:1], k - 1, axis=0), tgt])
    cs = np.cumsum(np.vstack([np.zeros((1, 3)), pad]), axis=0)
    ref = (cs[k:] - cs[:-k]) / k  # causal moving average

    # Critically damped spring x'' = w^2 (r - x) - 2 w x', exact step for piecewise-constant r.
    dt, w = 1.0 / FPS, OMEGA
    e = math.exp(-w * dt)
    pos = np.zeros((n, 3))
    x = ref[0].copy()
    v = np.zeros(3)
    for i in range(n):
        d = x - ref[i]
        c1 = d
        c2 = v + w * d
        d_new = (c1 + c2 * dt) * e
        v = (c2 - w * (c1 + c2 * dt)) * e
        x = ref[i] + d_new
        pos[i] = x

    # Hard guarantees: exactly wide inside cards / fast ranges, window inside the frame.
    # The spring leaves a few px of residue at the deadline; fade that out over 0.6 s.
    for b0, b1 in blocked:
        m = (t >= b0 - 0.6) & (t < b0)
        u = (t[m] - (b0 - 0.6)) / 0.6
        k = (u * u * (3 - 2 * u))[:, None]
        pos[m] = pos[m] * (1 - k) + np.array(WIDE) * k
        m = (t >= b0 - 1e-9) & (t < b1)
        pos[m] = WIDE
    pos[:, 2] = np.clip(pos[:, 2], W / MAX_ZOOM, W)
    pos[:, 0] = np.clip(pos[:, 0], 0, W - pos[:, 2])
    pos[:, 1] = np.clip(pos[:, 1], 0, H - pos[:, 2] * H / W)
    # Snap sub-0.02-px residues to exact wide so those frames pass through untouched.
    near = (np.abs(pos[:, 2] - W) < 0.02) & (np.abs(pos[:, 0]) < 0.02) & (np.abs(pos[:, 1]) < 0.02)
    pos[near] = WIDE
    return t, pos


def write_path(t, pos, zooms, blocked):
    os.makedirs(OUT, exist_ok=True)
    doc = {
        "fps": FPS, "width": W, "height": H, "frames": len(t),
        "params": {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))},
        "blocked": blocked,
        "zooms": zooms,
        "frames_xywh_s": [[round(p[0], 3), round(p[1], 3), round(p[2], 3), round(W / p[2], 4)] for p in pos],
    }
    with open(os.path.join(OUT, "zoom_path.json"), "w") as f:
        json.dump(doc, f, separators=(",", ":"))


def render(pos, n):
    import cv2
    cv2.setNumThreads(8)
    src = os.path.join(IN, "base.mp4")
    dst = os.path.join(OUT, "zoomed.mp4")
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-map", "0:v:0",
                            "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                           stdout=subprocess.PIPE, bufsize=W * H * 3)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                            "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", dst],
                           stdin=subprocess.PIPE)
    fsz = W * H * 3 // 2
    ysz = W * H
    csz = ysz // 4
    flags = cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP
    i = 0
    while True:
        buf = dec.stdout.read(fsz)
        if len(buf) < fsz:
            break
        x0, y0, w = pos[min(i, n - 1)]
        if w >= W - 1e-6 and x0 == 0 and y0 == 0:
            enc.stdin.write(buf)
        else:
            a = w / W  # source pixels per output pixel
            fr = np.frombuffer(buf, np.uint8)
            Y = fr[:ysz].reshape(H, W)
            U = fr[ysz:ysz + csz].reshape(H // 2, W // 2)
            V = fr[ysz + csz:].reshape(H // 2, W // 2)
            # Pixel-centre convention: src = x0 + (X + 0.5) * a - 0.5
            My = np.float32([[a, 0, x0 + 0.5 * a - 0.5], [0, a, y0 + 0.5 * a - 0.5]])
            Mc = np.float32([[a, 0, x0 / 2 + 0.5 * a - 0.5], [0, a, y0 / 2 + 0.5 * a - 0.5]])
            out = [cv2.warpAffine(Y, My, (W, H), flags=flags, borderMode=cv2.BORDER_REPLICATE),
                   cv2.warpAffine(U, Mc, (W // 2, H // 2), flags=flags, borderMode=cv2.BORDER_REPLICATE),
                   cv2.warpAffine(V, Mc, (W // 2, H // 2), flags=flags, borderMode=cv2.BORDER_REPLICATE)]
            enc.stdin.write(b"".join(p.tobytes() for p in out))
        i += 1
        if i % 1000 == 0:
            print(f"  {i}/{n} frames", file=sys.stderr)
    enc.stdin.close()
    dec.wait()
    enc.wait()
    if i != n:
        sys.exit(f"frame count mismatch: decoded {i}, expected {n}")
    print(f"wrote {dst} ({i} frames)")


# Representative moments for the visual check (final-video seconds).
CHECK_TIMES = [
    ("title_card", 4.0), ("demo_banner_wide", 15.0), ("zoom_who_spoke", 27.0), ("midpan_to_open_items", 28.6),
    ("row_click_wide", 37.5), ("jump_click_wide", 62.0), ("zoom_click_plays", 78.0), ("fast_range", 85.0),
    ("post_fast_wide", 106.0), ("clip_menus_wide", 118.0), ("public_clip_page", 133.0),
    ("zoom_answer_citing", 158.0), ("fast_range_2", 153.5), ("zoom_play_all", 197.5), ("zoom_out_play_all", 199.6),
    ("zoom_teams_and_leads", 231.0), ("fast_range_3", 246.0), ("fresh_notes_wide", 250.0), ("end_card", 260.0),
]


def checks():
    """Export stills at representative moments into OUT/checks (downscaled to 960 px)."""
    d = os.path.join(OUT, "checks")
    os.makedirs(d, exist_ok=True)
    for name, tt in CHECK_TIMES:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{tt:.3f}", "-i", os.path.join(OUT, "zoomed.mp4"),
                        "-frames:v", "1", "-vf", "scale=960:-1:flags=lanczos",
                        os.path.join(d, f"t{tt:06.2f}_{name}.png")], check=True)
    print(f"stills in {d}")


def preview(pos, zooms):
    """Plan-only visual check without rendering: for every zoom, crop base.mp4 with the
    planned window 1.5 s after it settles and at its end; PNGs in OUT/preview."""
    d = os.path.join(OUT, "preview")
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    for z in zooms:
        for tag, tt in (("a", min(z["t0"] + 1.5, z["t1"] - 0.2)), ("b", z["t1"] - 0.3)):
            x0, y0, w = pos[int(tt * FPS)]
            h = w * H / W
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{tt:.3f}", "-i", os.path.join(IN, "base.mp4"),
                            "-frames:v", "1", "-vf",
                            f"crop={w:.0f}:{h:.0f}:{x0:.0f}:{y0:.0f},scale=960:540:flags=lanczos,"
                            "drawbox=y=454:w=960:h=1:color=red@0.6,"
                            f"drawbox=x={BUBBLE[0] // 2}:y={BUBBLE[1] // 2}:w={(BUBBLE[2] - BUBBLE[0]) // 2}:"
                            f"h={(BUBBLE[3] - BUBBLE[1]) // 2}:color=blue@0.6",
                            os.path.join(d, f"t{tt:06.2f}{tag}_s{W / w:.2f}.png")], check=True)
    print(f"previews in {d} (red line = top of the subtitle zone, blue box = presenter bubble)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true", help="plan only (no render)")
    ap.add_argument("--checks", action="store_true", help="export check stills from the rendered file")
    ap.add_argument("--preview", action="store_true", help="plan only + cropped stills of each zoom from base.mp4")
    args = ap.parse_args()

    edit = load_edit()
    n = probe_frames()
    total = n / FPS
    ring_on, clear, ink = analyse(edit, n)
    keys, zooms, blocked = plan(edit, total, ring_on, clear, ink)
    t, pos = camera_path(keys, blocked, n)
    write_path(t, pos, zooms, blocked)
    for z in zooms:
        kind = "pan " if z["pan"] else "zoom"
        print(f"{kind} {z['t0']:7.2f}-{z['t1']:7.2f}  s={z['s']:.2f}  {' | '.join(x or '(no tag)' for x in z['tags'])}")
    print(f"{len(zooms)} targets, {n} frames")
    if args.checks:
        checks()
        return
    if args.preview:
        preview(pos, zooms)
        return
    if not args.plan:
        render(pos, n)


if __name__ == "__main__":
    main()
