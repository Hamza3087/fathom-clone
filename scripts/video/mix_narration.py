#!/usr/bin/env python3
"""Lays a recorded narration over the walkthrough video, block by block.
- cleans the voice (rumble filter, noise reduction, gentle compression, loudness to -16 LUFS)
- cuts the recording just before each block's first word (so trailing words stay in their block), trims dead air
- starts each block ~0.3 s after its section appears; a block that runs long is sped up by at most 5%,
  and beyond that the video holds on that screen until the sentence finishes (speech is never cut)
Usage: mix_narration.py <video.mp4> <video.marks.json> <narration audio> <words.json> <blocks.json> <out.mp4>"""
import json, subprocess, sys

video, marks_p, audio, words_p, blocks_p, out = sys.argv[1:7]
marks = [m for m in json.load(open(marks_p)) if m["kind"] in ("card", "card-off", "caption", "end")]
words, blocks = json.load(open(words_p)), json.load(open(blocks_p))
# Which caption/card starts each narration block (see docs/NARRATION.md), by position in the marks list.
START_MARK = [0, 2, 3, 4, 6, 7, 8, 11, 14, 17, 19, 23, 24, 25, 27, 29, 31, 32]
assert len(START_MARK) == len(blocks) == 18
v_start = [marks[i]["video_t"] for i in START_MARK]
v_end = v_start[1:] + [next(m["video_t"] for m in marks if m["kind"] == "end")]
MAX_SPEEDUP, LEAD, TAIL = 1.05, 0.3, 0.35

# Cut points in the recording: just before each block's first word.
cuts = [max(0.0, b["rec_start"] - 0.12) for b in blocks] + [words[-1][2] + 0.4]
segs = []
for i, b in enumerate(blocks):
    a, z = cuts[i], cuts[i + 1]
    spoken = [w for w in words if a <= w[1] < z]
    end = min(z, (spoken[-1][2] if spoken else b["rec_end"]) + 0.15)   # drop the pause before the next block
    segs.append((a if i else max(0.0, a - 0.2), end))

plan, t_new = [], 0.0
for i, (a, z) in enumerate(segs):
    window = v_end[i] - v_start[i]
    lead = 0.8 if i == 0 else LEAD
    dur = z - a
    speed = 1.0
    if dur + lead + TAIL > window:
        speed = min(MAX_SPEEDUP, (dur + lead + TAIL) / window)
    spoken = dur / speed
    hold = max(0.0, spoken + lead + TAIL - window)                     # extra seconds to freeze the picture
    plan.append(dict(block=i + 1, v0=v_start[i], v1=v_end[i], a0=a, a1=z, speed=round(speed, 3), hold=round(hold, 2),
                     at=round(t_new + lead, 3)))
    t_new += window + hold

vf, af = [], []
voice = "highpass=f=80,afftdn=nr=12:nf=-42,acompressor=threshold=-22dB:ratio=3:attack=5:release=150,loudnorm=I=-16:TP=-1.5:LRA=9"
for p in plan:
    k = p["block"]
    vf.append(f"[0:v]trim={p['v0']}:{p['v1']},setpts=PTS-STARTPTS" + (f",tpad=stop_mode=clone:stop_duration={p['hold']}" if p["hold"] else "") + f"[v{k}]")
    tempo = f",atempo={p['speed']}" if p["speed"] != 1.0 else ""
    af.append(f"[0:a]atrim={p['a0']}:{p['a1']},asetpts=PTS-STARTPTS{tempo},afade=t=in:d=0.04,"
              f"afade=t=out:st={max(0, (p['a1'] - p['a0']) / p['speed'] - 0.08):.3f}:d=0.08,adelay={int(p['at'] * 1000)}:all=1[a{k}]")
vf.append("".join(f"[v{p['block']}]" for p in plan) + f"concat=n={len(plan)}:v=1:a=0[v]")
af.append("".join(f"[a{p['block']}]" for p in plan) + f"amix=inputs={len(plan)}:normalize=0:duration=longest,{voice},apad=whole_dur={t_new:.3f}[a]")
track = out.rsplit(".", 1)[0] + ".voice.wav"
# 1) the voice track on its own, padded/cut to the exact length of the new video
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", audio, "-filter_complex", ";".join(af), "-map", "[a]",
                "-t", f"{t_new:.3f}", "-ar", "48000", track], check=True)
# 2) the re-timed video with that track
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", track, "-filter_complex", ";".join(vf),
                "-map", "[v]", "-map", "1:a", "-t", f"{t_new:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out], check=True)
for p in plan:
    print(f"block {p['block']:2d}: video {p['v0']:6.1f}-{p['v1']:6.1f}  voice {p['a0']:6.1f}-{p['a1']:6.1f}  speed x{p['speed']}  hold +{p['hold']}s")
print(f"total {t_new:.1f}s")
