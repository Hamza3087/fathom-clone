#!/usr/bin/env python3
"""Turns the screencast frames from record_walkthrough.mjs into an MP4.
Frames arrive only when the screen changes, so each frame is held until the next one. Stretches marked as
AI wait time are played 5x faster (the on-screen caption says "Sped up"), and any single frozen frame is capped.
Usage: build_video.py <frames_dir_parent> <out.mp4>"""
import json, os, subprocess, sys

src, out = sys.argv[1], sys.argv[2]
tl = json.load(open(os.path.join(src, "timeline.json")))
frames, fast = tl["frames"], tl["fastRanges"]
SPEED, MAX_HOLD = 5.0, 10.0


def in_fast(t):
    return any(a <= t < b for a, b in fast)


lines, total, starts = [], 0.0, []
for i, fr in enumerate(frames):
    starts.append(total)
    nxt = frames[i + 1]["t"] if i + 1 < len(frames) else fr["t"] + 2.0
    d = max(0.001, nxt - fr["t"])
    if in_fast(fr["t"]):
        d /= SPEED
    d = min(d, MAX_HOLD)
    total += d
    lines.append(f"file '{fr['f']}'\nduration {d:.4f}")
lines.append(f"file '{frames[-1]['f']}'")  # concat demuxer needs the last file repeated
lst = os.path.join(src, "frames.txt")
open(lst, "w").write("\n".join(lines) + "\n")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                "-vf", "fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", out], check=True)
# Caption and card times in the finished video, for writing the narration to length.
import bisect
ts = [f["t"] for f in frames]
marks = []
for m in tl.get("marks", []):
    i = max(0, bisect.bisect_right(ts, m["t"]) - 1)
    marks.append({**m, "video_t": round(starts[i] + min(m["t"] - ts[i], MAX_HOLD) / (SPEED if in_fast(ts[i]) else 1), 2)})
json.dump(marks, open(os.path.splitext(out)[0] + ".marks.json", "w"), indent=1)
print(f"{len(frames)} frames, {total / 60:.1f} min -> {out}")
