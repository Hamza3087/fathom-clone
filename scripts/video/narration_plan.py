#!/usr/bin/env python3
"""Works out, for each narration block, which part of the recording to use and how long it is.
The recorder paces each on-screen section to these lengths, so the voice never has to be sped up or cut.
- words said after a pause just before a block's first scripted word ("Back in, search runs…") belong to that block
- optional cuts (cuts.json: [[rec_start, rec_end], ...]) remove slips such as a repeated phrase
Usage: narration_plan.py <words.json> <blocks.json> <plan.json> [cuts.json]"""
import json, os, sys

words, blocks = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
cuts = json.load(open(sys.argv[4])) if len(sys.argv) > 4 and os.path.exists(sys.argv[4]) else []

starts = []
for i, b in enumerate(blocks):
    s = b["rec_start"]
    if i == 0:  # a greeting before the first scripted word ("Hello, I am Hamza") belongs to block 1
        first = next((w for w in words if not any(a <= w[1] < z for a, z in cuts)), None)
        if first and first[1] < s:
            s = first[1] + 0.15  # +0.15 offsets the extra lead-in below
    if i:
        prev_end = blocks[i - 1]["rec_end"]
        orphans = [w for w in words if prev_end < w[1] < b["rec_start"] and not any(a <= w[1] < z for a, z in cuts)]
        if orphans and orphans[0][1] - prev_end >= 0.5:
            s = orphans[0][1]  # a lead-in like "Back in," said after a pause
    starts.append(max(0.0, s - (0.15 + 0.12 if i == 0 else 0.12)))
starts.append(words[-1][2] + 0.4)

plan = []
for i, b in enumerate(blocks):
    a, z = starts[i], starts[i + 1]
    spoken = [w for w in words if a <= w[1] < z and not any(c0 <= w[1] < c1 for c0, c1 in cuts)]
    end = min(z, (spoken[-1][2] if spoken else b["rec_end"]) + 0.18)   # keep the last word, drop the pause after it
    mine = [[max(a, c0), min(end, c1)] for c0, c1 in cuts if c0 < end and c1 > a]
    dur = (end - a) - sum(c1 - c0 for c0, c1 in mine)
    plan.append(dict(block=b["n"], title=b["title"], a0=round(a, 3), a1=round(end, 3), cuts=[[round(x, 3) for x in c] for c in mine],
                     dur=round(dur, 3)))
json.dump(plan, open(sys.argv[3], "w"), indent=1)
print(" ".join(f"{p['block']}:{p['dur']:.1f}s" for p in plan), "| total voice", round(sum(p["dur"] for p in plan), 1), "s")
