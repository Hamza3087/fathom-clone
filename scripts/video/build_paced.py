#!/usr/bin/env python3
"""Builds the final narrated walkthrough from record_paced.mjs output.
- aligns the screen capture to the recorder's clock using the magenta sync flash
- fast-forwards the marked AI waits (x FF), fades in and out
- cleans the voice (rumble/hiss, de-ess, gentle compression, two-pass loudness to -16 LUFS)
- places every narration block at natural speed, LEAD seconds after its section starts
Usage: build_paced.py <capture_dir> <narration audio> <plan.json> <out.mp4>"""
import json, re, subprocess, sys

cap_dir, narration, plan_p, out = sys.argv[1:5]
cap = f"{cap_dir}/capture.mp4"
tl, plan = json.load(open(f"{cap_dir}/timeline.json")), json.load(open(plan_p))
FF = tl.get("FF", 5)
LEAD = lambda n: 1.0 if n == 1 else 0.45


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


# 1) where the sync flash is in the capture
raw = subprocess.check_output(["ffmpeg", "-loglevel", "error", "-t", "10", "-i", cap, "-vf", "crop=64:64:928:508,scale=1:1",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
idx = next(i for i in range(0, len(raw), 3) if raw[i] > 200 and raw[i + 1] < 90 and raw[i + 2] > 200) // 3
offset = idx / 30.0 - tl["flashT"]          # capture_time = wall_time + offset
print(f"sync flash at frame {idx} ({idx / 30:.2f}s)")

# 2) pieces of the capture: normal speed, with AI waits fast-forwarded
start, end = tl["blocks"][0]["t"], tl["endT"] + 0.9
ranges = [(r[0], r[1], r[2] if len(r) > 2 else FF) for r in tl["fastRanges"]]   # [start, end, speed]
cuts = sorted({start, end} | {x for a, b, _ in ranges for x in (a, b) if start < x < end})
pieces = []
for a, b in zip(cuts, cuts[1:]):
    k = next((rk for fa, fb, rk in ranges if fa <= a < fb), 1.0)
    pieces.append((a, b, k))


def final_t(w):
    t = 0.0
    for a, b, k in pieces:
        if w <= a: break
        t += (min(w, b) - a) / k
    return t


# Sections where the screen action runs well past the voice get their normal-speed parts played faster (max 1.8x),
# so each section ends about MARGIN seconds after its sentence instead of idling. AI waits keep their own speed.
MAX_TIGHTEN, MARGIN = 1.8, 0.8
bstarts = [b["t"] for b in tl["blocks"]] + [end]
factors = []
for i, b in enumerate(tl["blocks"]):
    room = final_t(bstarts[i + 1]) - final_t(bstarts[i])
    need = plan[i]["dur"] + LEAD(b["n"]) + MARGIN
    factors.append(min(MAX_TIGHTEN, room / need) if room - need > 1.5 else 1.0)
cuts2 = sorted(set(cuts) | {x for x in bstarts if start < x < end})
pieces = []
for a, b in zip(cuts2, cuts2[1:]):
    k = next((rk for fa, fb, rk in ranges if fa <= a < fb), None)
    if k is None:
        i = max(j for j in range(len(tl["blocks"])) if bstarts[j] <= a + 1e-9) if a >= bstarts[0] else 0
        k = factors[i]
    pieces.append((a, b, k))
print("tightened sections:", {tl["blocks"][i]["n"]: round(f, 2) for i, f in enumerate(factors) if f > 1})


total = final_t(end)
vf = []
for i, (a, b, k) in enumerate(pieces):
    vf.append(f"[0:v]trim={a + offset:.3f}:{b + offset:.3f},setpts=(PTS-STARTPTS)/{k}[p{i}]")
vf.append("".join(f"[p{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0,fps=30,"
          f"fade=t=in:st=0:d=0.6,fade=t=out:st={total - 1.1:.3f}:d=1.1,format=yuv420p[v]")

# 3) cleaned voice, two-pass loudness
chain = "highpass=f=85,lowpass=f=13000,afftdn=nr=10:nf=-45:tn=1,deesser=i=0.3,acompressor=threshold=-24dB:ratio=2.5:attack=8:release=220:makeup=2"
m = subprocess.run(["ffmpeg", "-hide_banner", "-i", narration, "-af", f"{chain},loudnorm=I=-16:TP=-1.5:LRA=8:print_format=json", "-f", "null", "-"],
                   capture_output=True, text=True).stderr
meas = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", m, re.S).group(0))
clean = out.rsplit(".", 1)[0] + ".clean.wav"
run(["ffmpeg", "-y", "-loglevel", "error", "-i", narration, "-af",
     f"{chain},loudnorm=I=-16:TP=-1.5:LRA=8:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:measured_LRA={meas['input_lra']}:"
     f"measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:linear=true", "-ar", "48000", clean])

# 4) voice blocks at natural speed
af, rows = [], []
starts = [final_t(b["t"]) for b in tl["blocks"]] + [total]
for i, (b, p) in enumerate(zip(tl["blocks"], plan)):
    at = starts[i] + LEAD(b["n"])
    # the block's audio, minus any cut slips, joined with 15 ms crossfades so the edits are inaudible
    keep, pos = [], p["a0"]
    for c0, c1 in p.get("cuts", []):
        keep.append((pos, c0)); pos = c1
    keep.append((pos, p["a1"]))
    keep = [(x, y) for x, y in keep if y - x >= 0.05]  # a cut at a block edge leaves an empty piece; ffmpeg would wait on it forever
    if len(keep) == 1:
        af.append(f"[0:a]atrim={p['a0']}:{p['a1']},asetpts=PTS-STARTPTS[s{i}]")
    else:
        for j, (x, y) in enumerate(keep):
            af.append(f"[0:a]atrim={x}:{y},asetpts=PTS-STARTPTS[s{i}_{j}]")
        chain = f"[s{i}_0]"
        for j in range(1, len(keep)):
            out_l = f"[s{i}]" if j == len(keep) - 1 else f"[s{i}_x{j}]"
            af.append(f"{chain}[s{i}_{j}]acrossfade=d=0.015:c1=tri:c2=tri{out_l}")
            chain = out_l
    af.append(f"[s{i}]afade=t=in:d=0.03,afade=t=out:st={max(0, p['dur'] - 0.06):.3f}:d=0.06,adelay={int(at * 1000)}:all=1[a{i}]")
    rows.append((b["n"], starts[i], p["dur"], starts[i + 1] - at))
af.append("".join(f"[a{i}]" for i in range(len(plan))) + f"amix=inputs={len(plan)}:normalize=0:duration=longest,apad=whole_dur={total:.3f}[a]")
voice = out.rsplit(".", 1)[0] + ".voice.wav"
run(["ffmpeg", "-y", "-loglevel", "error", "-i", clean, "-filter_complex", ";".join(af), "-map", "[a]", "-t", f"{total:.3f}", "-ar", "48000", voice])

# 5) final video
run(["ffmpeg", "-y", "-loglevel", "error", "-i", cap, "-i", voice, "-filter_complex", ";".join(vf), "-map", "[v]", "-map", "1:a",
     "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k",
     "-movflags", "+faststart", out])
# 6) edit.json: everything in final-video time, for the post-production passes (zoom, sound, graphics)
LEADS = {b["n"]: LEAD(b["n"]) for b in tl["blocks"]}
edit = dict(
    total=round(total, 3), fps=30, width=1920, height=1080, ff_speed=FF,
    blocks=[dict(n=b["n"], title=p["title"], start=round(starts[i], 3), end=round(starts[i + 1], 3),
                 voice_at=round(starts[i] + LEADS[b["n"]], 3), voice_dur=p["dur"], rec_a0=p["a0"], rec_a1=p["a1"],
                 cuts=p.get("cuts", []))
            for i, (b, p) in enumerate(zip(tl["blocks"], plan))],
    fast=[[round(final_t(a), 3), round(final_t(b), 3)] for a, b, _ in ranges],
    fast_speed=[k for _, _, k in ranges],
    captions=[dict(t=round(final_t(m["t"]), 3), step=m.get("step"), chapter=m.get("chapter"), text=m.get("text"), fast=m.get("fast", False))
              for m in tl["marks"] if m["kind"] == "caption"],
    cards=[dict(t=round(final_t(m["t"]), 3), kind=m["kind"]) for m in tl["marks"] if m["kind"] in ("card", "card-off")],
    focus=[dict(t=round(final_t(f["t"]), 3), rect=f["rect"], tag=f["tag"]) for f in tl.get("focus", [])],
)
json.dump(edit, open(out.rsplit(".", 1)[0] + ".edit.json", "w"), indent=1)
for n, s, d, room in rows:
    print(f"block {n:2d} starts {s:6.1f}s  voice {d:5.1f}s  room {room:5.1f}s" + ("  <-- voice longer than section" if d > room + 0.05 else ""))
print(f"total {total:.1f}s -> {out}")
