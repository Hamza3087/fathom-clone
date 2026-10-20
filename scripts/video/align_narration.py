#!/usr/bin/env python3
"""Aligns a recorded narration (word timings from Parakeet) to the blocks in docs/NARRATION.md.
Prints each block's span in the recording and the words heard there, and writes blocks.json."""
import difflib, json, re, sys

words = json.load(open(sys.argv[1]))           # [[word, start, end], ...]
script = open(sys.argv[2]).read()
out = sys.argv[3]

blocks = []
# headings: "### 3 · My calls" or, in older scripts, "### 3 · 0:14 – 0:29 · My calls"
for m in re.finditer(r"^### (\d+) · (?:(\d+):(\d+) – (\d+):(\d+) · )?(.+?)\n(.+?)(?=\n### |\Z)", script, re.S | re.M):
    n, a1, a2, b1, b2, title, text = m.groups()
    t0 = int(a1) * 60 + int(a2) if a1 else 0
    t1 = int(b1) * 60 + int(b2) if b1 else 0
    blocks.append(dict(n=int(n), title=title.strip(), start=t0, end=t1, text=" ".join(text.split())))

norm = lambda w: re.sub(r"[^a-z0-9]", "", w.lower())
s_tok, s_blk = [], []
for i, b in enumerate(blocks):
    for w in b["text"].split():
        if norm(w):
            s_tok.append(norm(w)); s_blk.append(i)
r_tok = [norm(w[0]) for w in words]
sm = difflib.SequenceMatcher(None, s_tok, r_tok, autojunk=False)
hits = {i: [] for i in range(len(blocks))}  # recording word indices matched to each block
for a, b, size in sm.get_matching_blocks():
    for k in range(size):
        hits[s_blk[a + k]].append(b + k)

prev_end = -1
for i, b in enumerate(blocks):
    idx = sorted(x for x in hits[i] if x > prev_end)
    if not idx:
        b.update(rec_start=None, rec_end=None, heard=""); continue
    lo, hi = idx[0], idx[-1]
    b.update(rec_start=words[lo][1], rec_end=words[hi][2], w_lo=lo, w_hi=hi, matched=len(idx),
             script_words=sum(1 for x in s_blk if x == i), heard=" ".join(w[0] for w in words[lo:hi + 1]))
    prev_end = hi
json.dump(blocks, open(out, "w"), indent=1)
for b in blocks:
    span = f"{b['rec_start']:6.1f}–{b['rec_end']:6.1f} ({b['rec_end'] - b['rec_start']:4.1f}s)" if b["rec_start"] is not None else "   not found"
    print(f"#{b['n']:<2} video {b['start']:3d}-{b['end']:3d} ({b['end'] - b['start']:2d}s) | rec {span} | {b.get('matched', 0)}/{b.get('script_words', 0)} words")
    print("     ", b["heard"][:400])
