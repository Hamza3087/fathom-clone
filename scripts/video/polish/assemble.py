#!/usr/bin/env python3
"""Final assembly: zoomed picture (zoom.py) + presenter camera bubble (cam_bubble.py) + burned-in graphics (graphics.py)
+ audio master (sound.py).
Usage: assemble.py [IN_DIR] [OUT_FILE]"""
import json, os, subprocess, sys
import numpy as np
import cv2

IN = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/-root/a2db516c-9100-4760-bff1-2f9fe28ddb93/scratchpad/polish"
OUT = sys.argv[2] if len(sys.argv) > 2 else f"{IN}/out/sounding-walkthrough-final.mp4"
total = json.load(open(f"{IN}/base.edit.json"))["total"]
vf = open(f"{IN}/out/graphics/filter.txt").read().strip()
cam = f"{IN}/out/cam/bubble.mp4"

# Bubble geometry (keep in sync with BRIEF.md): bottom-right, clear of subtitles and the progress bar.
D, CX, CY = 290, 1745, 905
art = f"{IN}/out/cam"
os.makedirs(art, exist_ok=True)
S = 4  # supersample for smooth edges
yy, xx = np.mgrid[0:D * S, 0:D * S]
r = np.hypot(xx - D * S / 2 + 0.5, yy - D * S / 2 + 0.5)
mask = cv2.resize(((r <= D * S / 2 - 2 * S) * 255).astype(np.uint8), (D, D), interpolation=cv2.INTER_AREA)
cv2.imwrite(f"{art}/mask.png", mask)
# ring + soft shadow on a canvas a little larger than the bubble
P = 40
C = D + 2 * P
yy, xx = np.mgrid[0:C * S, 0:C * S]
r = np.hypot(xx - C * S / 2 + 0.5, yy - C * S / 2 + 0.5) / S
ring = ((r >= D / 2 - 2) & (r <= D / 2 + 2.5)).astype(np.float32)
shadow = np.clip(1 - (r - D / 2) / 22, 0, 1) * (r > D / 2) * 0.35
rgba = np.zeros((C * S, C * S, 4), np.float32)
teal = np.array([0xc9, 0xc2, 0x57], np.float32)  # BGR of #57c2c9
rgba[..., :3] = ring[..., None] * teal
rgba[..., 3] = np.maximum(ring, shadow) * 255
cv2.imwrite(f"{art}/frame.png", cv2.resize(rgba, (C, C), interpolation=cv2.INTER_AREA).astype(np.uint8))

fc = (f"[1:v]scale={D}:{D}:flags=lanczos,format=rgba[cam];[3:v]format=gray[m];[cam][m]alphamerge[bub];"
      f"[0:v][4:v]overlay={CX - C // 2}:{CY - C // 2}+4[sh];[sh][bub]overlay={CX - D // 2}:{CY - D // 2}[v1];"
      f"[v1]{vf},format=yuv420p[v]")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{IN}/out/zoom/zoomed.mp4", "-i", cam, "-i", f"{IN}/out/sound/mix.wav",
                "-loop", "1", "-i", f"{art}/mask.png", "-loop", "1", "-i", f"{art}/frame.png",
                "-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-t", f"{total:.3f}",
                "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high", "-tune", "film",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", OUT], check=True)
print(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration:format=duration,size", "-of", "compact", OUT], text=True).strip())
print("->", OUT)
