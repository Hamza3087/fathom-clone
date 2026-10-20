#!/usr/bin/env python3
"""Graphics pass: burn-in subtitles, chapter chips, fast-forward badges and a progress bar.

Usage:
    python3 graphics.py [IN]        # IN defaults to the scratchpad polish directory (see BRIEF.md)

Reads IN/base.edit.json, ../words.json (ASR word timings of the original recording) and docs/NARRATION.md.
Writes IN/out/graphics/:
    overlay.ass     all graphics as one libass script (1920x1080, fixed screen positions)
    subtitles.srt   plain subtitles for upload platforms
    cues.json       the subtitle cues with word-level detail (for checking)
    filter.txt      the ffmpeg -vf chain that burns overlay.ass onto a 1920x1080 video
    fonts/          Schibsted Grotesk static instances (made from the Google Fonts variable font, OFL)

Subtitle text: ASR words are aligned per narration block to the script (Needleman-Wunsch on words with a
fuzzy similarity). Matched and near-matched words take the script's spelling, case and punctuation; words he
said that aren't in the script keep the ASR text; script words he didn't say are dropped. A short curated list
(ASR_FIXES) fixes recognition errors too far from the script for the fuzzy match, removes self-corrections and
restores small words he swallowed. Spelling is US English, to match the app.

Timing: recording time -> final time per block (voice_at + t - rec_a0), minus any cuts earlier in the block
(blocks[].cuts, or IN/plan_cuts.json); words inside a cut are dropped. Each cue starts 0.1 s before the voice
onset measured in base.voice.wav, since ASR start times after a pause can be late.
The script prints warnings for graphics that cover an orange highlight ring, overlap the presenter camera bubble
(BUBBLE, bottom right) or, for subtitles, reach past SUB_MAX_X, and for inconsistent punctuation. The fast badge sits
just above the bubble, right-aligned with it; the chapter chip stays bottom left.
"""
import difflib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = "/tmp/claude-0/-root/a2db516c-9100-4760-bff1-2f9fe28ddb93/scratchpad"
IN = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else f"{SCRATCH}/polish")
OUT = f"{IN}/out/graphics"
FONTS = f"{OUT}/fonts"
WORDS = f"{os.path.dirname(IN)}/words.json"
SCRIPT = os.path.join(HERE, "../../../docs/NARRATION.md")

E = json.load(open(f"{IN}/base.edit.json"))
PLAN_CUTS = [tuple(c) for c in json.load(open(f"{IN}/plan_cuts.json"))] if os.path.exists(f"{IN}/plan_cuts.json") else []
W, H, TOTAL = E["width"], E["height"], E["total"]

# ---------------------------------------------------------------- fonts
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/schibstedgrotesk/"
SUB_FONT, UI_FONT = "Schibsted Grotesk SemiBold", "Schibsted Grotesk Medium"


def make_fonts():
    """Downloads the variable font from the Google Fonts repo and cuts static 600/500 instances (libass can't
    pick a weight from a variable font). Returns an advance-width measurer, or None to fall back to DejaVu."""
    os.makedirs(f"{FONTS}/src", exist_ok=True)
    var = f"{FONTS}/src/SchibstedGrotesk[wght].ttf"
    try:
        if not os.path.exists(var):
            import urllib.request
            urllib.request.urlretrieve(FONT_URL + "SchibstedGrotesk%5Bwght%5D.ttf", var)
            urllib.request.urlretrieve(FONT_URL + "OFL.txt", f"{FONTS}/OFL.txt")
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont
    except Exception as e:
        print("font unavailable, falling back to DejaVu Sans:", e)
        return None
    faces = {}
    for weight, style in [(600, "SemiBold"), (500, "Medium")]:
        path = f"{FONTS}/SchibstedGrotesk-{style}.ttf"
        if not os.path.exists(path):
            f = instantiateVariableFont(TTFont(var), {"wght": weight})
            names = f["name"]
            for nid in (16, 17, 25):
                names.removeNames(nameID=nid)
            for nid, val in [(1, f"Schibsted Grotesk {style}"), (2, "Regular"), (4, f"Schibsted Grotesk {style}"),
                             (6, f"SchibstedGrotesk-{style}")]:
                names.setName(val, nid, 3, 1, 0x409)
            f["OS/2"].usWeightClass = 400  # its own family, so libass never synthesises bold
            f.save(path)
        f = TTFont(path)
        faces[f"Schibsted Grotesk {style}"] = (f.getBestCmap(), f["hmtx"].metrics,
                                                f["OS/2"].usWinAscent + f["OS/2"].usWinDescent)
    return faces


FACES = make_fonts()
if FACES is None:
    SUB_FONT = UI_FONT = "DejaVu Sans"


def em_to_fs(em):
    """ASS \\fs for a given em size in px: libass sizes a font by winAscent+winDescent, not by the em."""
    if FACES is None:
        return round(em * 1.16)
    f = FACES[SUB_FONT]
    return round(em * f[2] / 2048)


def text_width(text, font, fs):
    if FACES is None:
        return len(text) * fs * 0.55
    cmap, hmtx, units = FACES[font]
    w = sum(hmtx[cmap.get(ord(c), cmap[ord("n")])][0] for c in text)
    return w * fs / units


# ---------------------------------------------------------------- subtitle text
# Recognition errors too far from the script for the fuzzy alignment (round 3 on-camera take). Matched on ASR
# text, punctuation included, in this order.
ASR_FIXES = [
    ("Faitham,", "Fathom:"), ("Ritham,", "Fathom,"), ("Amza.", "Hamza."),
    ("note taker. Data meeting, recording", "notetaker that turns a meeting recording"),
    ("sounding", "Sounding"), ("founding", "Sounding"), ("Meetbot", "Meet bot"),
    ("heated", "seeded"), ("an R.", "an hour."), ("one-line summarizers,", "one-line summaries,"), ("with 8%.", "with eight people."),
    ("added second part of the clusters. Voices, it", "added a second pass that clusters voices. It"),
    ("lines back to where it said.", "links back to where it was said."),
    ("like sale calls, actions, items, keep", "like a sales call. Action items keep"),
    ("The more answers comes back the timestamps", "The answer comes back with timestamps,"),
    ("you exact one point in the cell.", "you to the exact point in the call."),
    ("at matching mode.", "at the matching moment."), ("next step. And answers links", "next steps, and the answer links"),
    ("the line like until early May 2000 in their paper. Even", 'lines like "until early May to turn in their paper", even'),
    ("scoreboards,", "scorecards,"), ("scoreboard", "scorecard"), ("e-meeting", "meeting"), ("Peer platform", "per platform"),
    ("process is there this is admin side", "processes, this is the admin side."),
    ("from the upload to title speaker split", "from upload to titled, speaker-split"),
    ("I had built", "I'd build"),
    # light grammar clean-up (QA): the articles and small words he swallowed or doubled
    ("The live opens", "The live link opens"), ("sign up", "sign-up"),
    ("were generated by pipelines.", "was generated by the pipeline:"),
    ("who spoke, how many actions and items", "who spoke and how many action items"), ("actions, items,", "action items"),
    ("press the play,", "press play,"), ("along the recorded lines.", "along with the recording."),
    ("cites at a time", "cites a time"), ("rewrites notes", "rewrites the notes"), ("and when you can", "and when, and you can"),
    ("meeting question.", "meeting a question."), ("saves last", "saves the last"),
    ("next 10 seconds because the time", "next ten, because by the time"), ("from transcript and", "from the transcript, and"),
    ("look for the meaning.", "look for meaning."), ("criteria of set", "criteria set"),
    ("private shared with team or shared with whole", "private, shared with a team or shared with the whole"),
    ("leads setup meeting times", "leads, and set up meeting types"),
    ("for the company words and transcripts", "for company words the transcript"),
]
# Where he deliberately said more than the script: edit the script so alignment keeps the script's punctuation.
SCRIPT_FIXES = [("seven speakers, with", "seven speakers successfully, with")]
US_SPELLING = {"organisation": "organization", "organisations": "organizations", "summarised": "summarized",
               "labelled": "labeled", "recognised": "recognized", "prioritise": "prioritize"}
DROP_AT = []  # [(recording t, word)]: stray words to drop before alignment
NAMES = {"hamza": "Hamza", "sounding": "Sounding", "fathom": "Fathom", "qwen": "Qwen", "parakeet": "Parakeet",
         "sortformer": "Sortformer", "titanet": "TitaNet", "google": "Google", "meet": "Meet", "spiced": "SPICED",
         "ai": "AI", "i": "I", "friday": "Friday"}
EXPAND = {"I'm": "I am", "wasn't": "was not", "doesn't": "does not"}  # he says these in full; compare like with like
NUM = {"4": "four", "7": "seven", "8": "eight", "10": "ten", "20": "twenty"}
SHOW_NUM = {"twenty": "20"}  # style: numbers above ten as digits


def us_spelling(text):
    def fix(m):
        us = US_SPELLING.get(m[0].lower())
        return m[0] if us is None else us.capitalize() if m[0][0].isupper() else us
    return re.sub(r"[A-Za-z]+", fix, text)


def norm(w):
    w = re.sub(r"[^a-z0-9'-]", "", w.lower().replace("’", "'")).strip("-'")
    return NUM.get(w, w)


def sim(a, b):
    a, b = norm(a), norm(b)
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def block_cuts(b):
    """Recording-time ranges cut out of this block's narration: blocks[].cuts if the edit has them, else the
    ranges from IN/plan_cuts.json that fall inside the block."""
    if "cuts" in b:
        return sorted(tuple(c) for c in b["cuts"])
    return sorted((c0, c1) for c0, c1 in PLAN_CUTS if b["rec_a0"] <= (c0 + c1) / 2 <= b["rec_a1"])


def rec_to_final(b, t):
    """Recording time -> final-video time, closing up any cuts earlier in the block."""
    removed = sum(max(0.0, min(t, c1) - c0) for c0, c1 in block_cuts(b) if c0 < t)
    return b["voice_at"] + (t - b["rec_a0"]) - removed


def in_cut(w):
    """A word that is mostly inside a cut range was removed from the audio (the spoken section headings).
    Words between blocks are dropped anyway, since build_words only takes words inside rec_a0..rec_a1."""
    cuts = [c for b in E["blocks"] for c in block_cuts(b)]
    return any(min(w[2], c1) - max(w[1], c0) > 0.5 * (w[2] - w[1]) for c0, c1 in cuts)


def load_asr():
    words = [w for w in json.load(open(WORDS))
             if not in_cut(w) and not any(abs(w[1] - t) < 0.05 and w[0] == s for t, s in DROP_AT)]
    for bad, good in ASR_FIXES:
        bad, good = bad.split(), good.split()
        i = 0
        while i <= len(words) - len(bad):
            if [w[0] for w in words[i:i + len(bad)]] == bad:
                t0, t1 = words[i][1], words[i + len(bad) - 1][2]
                total = sum(len(g) + 1 for g in good)
                new, t = [], t0
                for g in good:  # spread the span over the new words by length
                    d = (t1 - t0) * (len(g) + 1) / total
                    new.append([g, round(t, 3), round(t + d, 3)])
                    t += d
                words[i:i + len(bad)] = new
                i += len(good)
            else:
                i += 1
    return words


def script_blocks():
    txt = open(SCRIPT).read()
    for a, b in SCRIPT_FIXES:
        txt = txt.replace(a, b)
    return [m.group(2) for m in re.finditer(r"^### (\d+) · [^\n]*\n(.+?)\n", txt, re.M)]


def align(a, s):
    """Needleman-Wunsch over words. Returns [(i or None, j or None)] pairs."""
    GAP = -0.6
    score = lambda x, y: (2 * sim(x, y) - 0.8) if sim(x, y) >= 0.5 else -1.2
    n, m = len(a), len(s)
    D = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1): D[i][0] = i * GAP
    for j in range(1, m + 1): D[0][j] = j * GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = max(D[i - 1][j - 1] + score(a[i - 1], s[j - 1]), D[i - 1][j] + GAP, D[i][j - 1] + GAP)
    out, i, j = [], n, m
    while i or j:
        if i and j and abs(D[i][j] - (D[i - 1][j - 1] + score(a[i - 1], s[j - 1]))) < 1e-9:
            out.append((i - 1, j - 1)); i -= 1; j -= 1
        elif i and abs(D[i][j] - (D[i - 1][j] + GAP)) < 1e-9:
            out.append((i - 1, None)); i -= 1
        else:
            out.append((None, j - 1)); j -= 1
    return out[::-1]


def ends_sentence(w):
    return w.rstrip('"”').endswith((".", "?", "!"))


def build_words():
    """Final-video word list [{w, t0, t1, block, src}] with corrected text."""
    asr, scripts, words = load_asr(), script_blocks(), []
    for b in E["blocks"]:
        a0, a1 = b["rec_a0"], b["rec_a1"]
        mine = [w for w in asr if a0 <= (w[1] + w[2]) / 2 <= a1]
        s = " ".join(EXPAND.get(t, t) for t in scripts[b["n"] - 1].split()).split()
        pairs = align([w[0] for w in mine], s)
        out = []
        for k, (i, j) in enumerate(pairs):
            if i is None:
                # script word he didn't say: dropped. If it was an "and" joining a list or two clauses, keep his
                # punctuation for the word before it.
                if s[j] == "and" and out and out[-1]["src"] == "script" and out[-1]["asr"][-1] in ",.":
                    out[-1]["w"] = out[-1]["w"].rstrip(",.") + out[-1]["asr"][-1]
                continue
            w = mine[i]
            if j is not None and sim(w[0], s[j]) >= 0.5:
                text, src = s[j], "script"
                if norm(s[j]) in SHOW_NUM:
                    text = re.sub(r"[A-Za-z]+", SHOW_NUM[norm(s[j])], s[j])
            else:
                text, src = w[0], "asr"
                core = norm(text)
                if core in NAMES:
                    text = re.sub(re.escape(core), NAMES[core], text, flags=re.I)
                prev = out[-1] if out else None
                if prev and prev["src"] == "script" and ends_sentence(prev["w"]) and not ends_sentence(prev["asr"]):
                    prev["w"] = prev["w"].rstrip(".")  # the script ended the sentence here; he carried on
                elif prev is None or ends_sentence(prev["w"]):
                    text = text[0].upper() + text[1:]
                elif core not in NAMES:
                    text = text[0].lower() + text[1:] if not text[:2].isupper() else text
            if src == "script" and out and ends_sentence(out[-1]["w"]) and text[0].islower():
                text = text[0].upper() + text[1:]
            t0, t1 = rec_to_final(b, w[1]), rec_to_final(b, min(w[2], a1 + 0.3))
            text = us_spelling(text)
            text = re.sub(r'^"', "“", re.sub(r'"(?=\W*$)', "”", text))  # curly quotes
            out.append(dict(w=text, asr=w[0], src=src, t0=round(t0, 3), t1=round(t1, 3), block=b["n"]))
        if out and not ends_sentence(out[-1]["w"]):
            out[-1]["w"] = out[-1]["w"].rstrip(",:;") + "."
        words += out
    return words


# ---------------------------------------------------------------- segmentation
MAX_LINE, MAX_DUR, MIN_DUR = 42, 6.0, 1.0
WEAK_END = {"a", "an", "the", "to", "of", "for", "with", "and", "or", "from", "by", "in", "on", "at", "that", "my",
            "your", "each", "every", "is", "are", "was", "who", "how", "what", "so", "i", "it's", "any", "just",
            "even", "where", "when", "can", "will", "would", "because", "but", "than"}
NOBREAK = {("google", "meet"), ("meet", "bot"), ("log", "out"), ("ask", "sounding"), ("play", "all"),
           ("meeting", "types"), ("twenty", "seconds"), ("next", "ten"), ("by", "time"), ("each", "meeting"),
           ("meeting", "recording"), ("team", "leads"), ("live", "notes"), ("the", "code"),
           ("results", "open"), ("work", "per"), ("you", "think"), ("it", "goes"), ("app", "reviews"), ("custom", "templates"),
           ("demo", "workspace"), ("tested", "it"), ("agent", "logs"), ("against", "human"), ("second", "pass"), ("it", "against"), ("clusters", "voices")}
STARTS = {"and", "but", "so", "or", "because", "which", "while", "then"}  # good words to start a line or cue with


def join(ws):
    return " ".join(w["w"] for w in ws)


def split_lines(ws):
    """Best 1- or 2-line layout of a word list, or None if it doesn't fit."""
    text = join(ws).rstrip(".,;:")
    if len(text) <= MAX_LINE:
        return [text], 0.0
    best = None
    for k in range(1, len(ws)):
        l1, l2 = join(ws[:k]), join(ws[k:]).rstrip(".,;:")
        if len(l1) > MAX_LINE or len(l2) > MAX_LINE:
            continue
        cost = abs(len(l1) - len(l2)) / 10
        last = ws[k - 1]["w"]
        if ends_sentence(last):
            cost -= 2.5
        elif last.endswith((",", ":", ";")):
            cost -= 1.5
        if norm(last) in WEAK_END:
            cost += 3.0
        if (norm(last), norm(ws[k]["w"])) in NOBREAK:
            cost += 8.0
        if norm(ws[k]["w"]) in STARTS:
            cost -= 0.8
        if l1.count("“") > l1.count("”"):
            cost += 4.0  # inside a quotation
        if any(ends_sentence(w["w"]) for w in ws[:k - 1]) and not ends_sentence(last):
            cost += 1.5  # a line that ends a sentence and starts the next one
        if len(l1) > len(l2) + 8:
            cost += 0.3  # prefer a bottom-heavy pyramid
        if best is None or cost < best[1]:
            best = ([l1, l2], cost)
    return best


def break_cost(ws, k):
    """Cost of ending a cue after word k."""
    w, nxt = ws[k], ws[k + 1] if k + 1 < len(ws) else None
    if nxt is None or nxt["t0"] - w["t1"] > 1.2 or nxt["block"] != w["block"]:
        return 0.0
    gap = nxt["t0"] - w["t1"]
    if ends_sentence(w["w"]):
        c = 0.0
    elif w["w"].endswith((",", ":", ";")):
        c = 1.0
    elif gap > 0.3:
        c = 1.5
    else:
        c = 5.0
    if norm(w["w"]) in WEAK_END:
        c += 3.0
    if (norm(w["w"]), norm(nxt["w"])) in NOBREAK:
        c += 5.0
    if sum(x["w"].count("“") - x["w"].count("”") for x in ws[:k + 1]) > 0:
        c += 4.0  # inside a quotation
    return c - min(gap, 0.6)


def lead_in(ws, k):
    """True if a sentence ends at word k and the rest of the block is a few words that begin the next block's
    sentence (e.g. "Back in," said just before the cut). Those get their own cue."""
    if not ends_sentence(ws[k]["w"]):
        return False
    rest = [w for w in ws[k + 1:] if w["block"] == ws[k]["block"]]
    return 0 < len(rest) <= 3 and not ends_sentence(rest[-1]["asr"]) and k + 1 + len(rest) < len(ws)


def segment(ws):
    """Dynamic programme over cue boundaries."""
    INF = float("inf")
    best = [0.0] + [INF] * len(ws)
    back = [0] * (len(ws) + 1)
    for j in range(1, len(ws) + 1):
        for i in range(j - 1, -1, -1):
            seg = ws[i:j]
            if seg[-1]["t1"] - seg[0]["t0"] > MAX_DUR - 0.2:
                break
            if any(seg[k + 1]["t0"] - seg[k]["t1"] > 1.2 or seg[k + 1]["block"] != seg[k]["block"]
                   or lead_in(ws, i + k) for k in range(len(seg) - 1)):
                break  # never run a cue across a pause or a cut between narration blocks
            lay = split_lines(seg)
            if lay is None:
                break
            dur = seg[-1]["t1"] - seg[0]["t0"]
            n = len(join(seg))
            c = 2.0 + break_cost(ws, j - 1) + lay[1]
            whole = ends_sentence(seg[-1]["w"]) and (i == 0 or ends_sentence(ws[i - 1]["w"]))
            if not whole:  # a short complete sentence is a fine cue; a short fragment isn't
                c += 2.0 if n < 22 else 0.0
                c += 2.0 if dur < MIN_DUR else 0.0
            c += 0.5 * max(0, dur - 5.0)
            tail = next((len(join(seg[k + 1:])) for k in range(len(seg) - 2, -1, -1)
                         if ends_sentence(seg[k]["w"])), None)
            if tail is not None and not ends_sentence(seg[-1]["w"]) and tail < 25:
                c += 6.0  # a cue that ends a sentence and then starts only a few words of the next one
            if best[i] + c < best[j]:
                best[j], back[j] = best[i] + c, i
    cuts, j = [], len(ws)
    while j:
        cuts.append((back[j], j)); j = back[j]
    return [ws[i:j] for i, j in cuts[::-1]]


def tidy(line, last):
    line = line.replace("'", "’")
    if last:
        line = re.sub(r"[.,;:]+(”?)$", r"\1", line)
    return line


def voice_activity():
    """10 ms frames of base.voice.wav above -40 dBFS (the narration alone, on the final timeline), or None."""
    path = f"{IN}/base.voice.wav"
    if not os.path.exists(path):
        return None
    import numpy as np, subprocess
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = len(x) // 160
    return 10 * np.log10(np.mean(x[:n * 160].reshape(n, 160) ** 2, 1) + 1e-10) > -40


def voice_onset(act, t, lo):
    """Where the voice actually starts for a word the ASR places at t: the end of the last >=150 ms silence
    between lo and t. ASR start times after a pause inside a block are often 0.3-0.9 s late."""
    if act is None:
        return t
    k, lo_k, run = min(int(t * 100) + 5, len(act) - 1), max(0, int(lo * 100)), 0
    while k > lo_k:
        run = run + 1 if not act[k] else 0
        if run >= 15:
            return min(t, (k + run) / 100)
        k -= 1
    return min(t, lo)


def make_cues(words):
    cues, act = [], voice_activity()
    for seg in segment(words):
        lines = split_lines(seg)[0]
        lines = [us_spelling(tidy(l, k == len(lines) - 1)) for k, l in enumerate(lines)]
        prev = cues[-1]["words"][-1] if cues else None
        lo = prev["t1"] + 0.05 if prev and seg[0]["t0"] - prev["t1"] < 1.5 else seg[0]["t0"] - 0.5
        start = voice_onset(act, seg[0]["t0"], lo) - 0.1
        cues.append(dict(t0=start, t1=seg[-1]["t1"] + 0.25, lines=lines, words=seg))
    for n, (a, b) in enumerate(zip(cues, cues[1:])):
        a["t1"] = min(a["t1"], b["t0"])
        short = MIN_DUR - (a["t1"] - a["t0"])
        if short > 0:  # too short to read: hold it longer, then start a little earlier, then nudge the next cue
            a["t1"] = min(a["t0"] + MIN_DUR, b["t0"])
            short = MIN_DUR - (a["t1"] - a["t0"])
            room = a["t0"] - (cues[n - 1]["t1"] if n else 0)
            a["t0"] -= max(0, min(short, room, 0.3))
            a["t1"] = a["t0"] + MIN_DUR
            b["t0"] = max(b["t0"], min(a["t1"], b["t0"] + 0.25))
        if b["t0"] - a["t1"] < 0.25 or a["t1"] > b["t0"]:
            a["t1"] = b["t0"]  # close tiny gaps and avoid overlaps
    last = cues[-1]
    last["t1"] = min(TOTAL, max(last["t1"], last["t0"] + MIN_DUR))
    for c in cues:
        c["t0"], c["t1"] = round(max(0, c["t0"]), 2), round(c["t1"], 2)
    return cues


# ---------------------------------------------------------------- ASS helpers
def ts(t):
    t = max(0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def col(hexrgb, alpha=0):
    r, g, b = hexrgb[1:3], hexrgb[3:5], hexrgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}&".upper()


def rrect(x0, y0, x1, y1, r):
    """ASS drawing of a rounded rectangle."""
    k = r * 0.4477  # 1 - 0.5523, cubic approximation of a quarter circle
    f = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")
    p = [f"m {f(x0 + r)} {f(y0)}", f"l {f(x1 - r)} {f(y0)}", f"b {f(x1 - k)} {f(y0)} {f(x1)} {f(y0 + k)} {f(x1)} {f(y0 + r)}",
         f"l {f(x1)} {f(y1 - r)}", f"b {f(x1)} {f(y1 - k)} {f(x1 - k)} {f(y1)} {f(x1 - r)} {f(y1)}",
         f"l {f(x0 + r)} {f(y1)}", f"b {f(x0 + k)} {f(y1)} {f(x0)} {f(y1 - k)} {f(x0)} {f(y1 - r)}",
         f"l {f(x0)} {f(y0 + r)}", f"b {f(x0)} {f(y0 + k)} {f(x0 + k)} {f(y0)} {f(x0 + r)} {f(y0)}"]
    return " ".join(p)


TEAL, LTEAL, INK, PAPER, ORANGE = "#0f6b73", "#57c2c9", "#0a181c", "#f4f6f5", "#ff7a1a"
events = []
BOXES = []  # (t0, t1, x0, y0, x1, y1, what) of every opaque graphic, for the clash check


def ev(layer, t0, t1, style, text):
    if t1 - t0 > 0.02:
        events.append(f"Dialogue: {layer},{ts(t0)},{ts(t1)},{style},,0,0,0,,{text}")


def draw(layer, t0, t1, path, color, alpha, tags="\\pos(0,0)"):
    """A vector shape in absolute pixel coordinates; `tags` carries the \\pos or \\move plus any fade/blur."""
    ev(layer, t0, t1, "Draw", f"{{\\an7\\bord0\\shad0\\1c{col(color)}\\1a&H{alpha:02X}&{tags}\\p1}}{path}")


# ---------------------------------------------------------------- the graphics
SUB_EM, SUB_PITCH, SUB_PADX, SUB_PADY, SUB_BOTTOM = 40, 50, 24, 12, 70
UI_EM = 22
CHIP_X, CHIP_Y = 32, 1080 - 5 - 16 - 38  # bottom left, mirroring the fast badge; clear of the header and titles
CAP = 1440 / 2048  # cap height / em of Schibsted Grotesk
BUBBLE = (1590, 750, 1900, 1065)  # presenter camera bubble with its ring and shadow (x0, y0, x1, y1): keep out
SUB_MAX_X = 1560  # no subtitle may reach further right than this


def baseline_to_an(base, fs):
    """y for \\an1/\\an2 that puts the baseline at `base` (libass anchors the bottom at baseline + winDescent)."""
    return base + fs * (568 / 3070 if FACES else 0.24)


def cards_off():
    """[(start, end)] of full-screen cards."""
    spans, on = [], None
    for c in sorted(E["cards"], key=lambda c: c["t"]):
        if c["kind"] == "card":
            on = c["t"]
        elif on is not None:
            spans.append((on, c["t"])); on = None
    if on is not None:
        spans.append((on, TOTAL))
    return spans


CARDS = cards_off()
SHOW_FROM, SHOW_TO = CARDS[0][1], CARDS[-1][0]  # graphics live between the title card and the end card


def subtitles(cues):
    fs = em_to_fs(SUB_EM)
    for n, c in enumerate(cues):
        prev_gap = c["t0"] - cues[n - 1]["t1"] if n else 9
        next_gap = cues[n + 1]["t0"] - c["t1"] if n + 1 < len(cues) else 9
        fade = f"\\fad({120 if prev_gap > 0.05 else 0},{120 if next_gap > 0.05 else 0})"
        widths = [text_width(l, SUB_FONT, fs) for l in c["lines"]]
        bw = max(widths) + 2 * SUB_PADX
        y1 = H - SUB_BOTTOM
        y0 = y1 - len(c["lines"]) * SUB_PITCH - 2 * SUB_PADY
        draw(0, c["t0"], c["t1"], rrect(W / 2 - bw / 2, y0, W / 2 + bw / 2, y1, 12), INK, 0x26, "\\pos(0,0)" + fade)
        BOXES.append((c["t0"], c["t1"], W / 2 - bw / 2, y0, W / 2 + bw / 2, y1, "subtitle: " + c["lines"][0]))
        for k, line in enumerate(c["lines"]):
            # one event per line, so the line pitch is ours rather than libass's; caps centred in each line
            base = y0 + SUB_PADY + k * SUB_PITCH + (SUB_PITCH + CAP * SUB_EM) / 2
            ev(1, c["t0"], c["t1"], "Sub", f"{{\\an2\\pos({W / 2:.0f},{baseline_to_an(base, fs):.1f}){fade}}}{line}")


def chapters():
    """Chapter starts [(t, step, label)] from captions[], names tidied in sentence case like the app UI."""
    names = {"The hard case: 1 hour, 8 people": "The hour-long, 8-person call",
             "Ask about this meeting": "Ask this meeting", "Across all meetings": "Search and Ask across meetings"}
    out, last = [], None
    for c in E["captions"]:
        if c["chapter"] != last:
            name = names.get(c["chapter"], c["chapter"])
            out.append((c["t"], c["step"], us_spelling(name[:1].upper() + name[1:])))
            last = c["chapter"]
    return out


FOCUS = sorted(E["focus"], key=lambda f: f["t"])


def ring_at(t):
    ring = next((f for f in reversed(FOCUS) if f["t"] <= t), None)
    return ring if ring and ring["rect"] else None


def covers_ring(box, ring):
    """True if box (x0, y0, x1, y1) covers the ring's outline (with its glow) or its label, drawn above the ring."""
    x0, y0, x1, y1 = box
    r = ring["rect"]
    rx0, ry0, rx1, ry1 = r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"]
    band = x0 < rx1 + 8 and x1 > rx0 - 8 and y0 < ry1 + 8 and y1 > ry0 - 8
    inside = x0 > rx0 + 8 and x1 < rx1 - 8 and y0 > ry0 + 8 and y1 < ry1 - 8
    ly0, ly1 = (ry0 - 38, ry0) if ry0 >= 40 else (ry1, ry1 + 38)  # the label sits above, or below if no room
    label = x0 < rx0 + 24 + 10 * len(ring["tag"] or "") and x1 > rx0 and y0 < ly1 and y1 > ly0
    return (band and not inside) or label


def pill(t0, t1, x, y, w, h, anim, what, layer=2):
    """Near-black rounded pill with a soft drop shadow. `anim(x)` gives the \\pos/\\move and fade tags."""
    draw(layer, t0, t1, rrect(x, y + 3, x + w, y + h + 3, h / 2), "#000000", 0xB8, anim(0) + "\\blur6")
    draw(layer, t0, t1, rrect(x, y, x + w, y + h, h / 2), INK, 0x14, anim(0))
    BOXES.append((t0, t1, x, y, x + w, y + h, what))


def chip(t, step, label):
    """Chapter chip at the bottom left, just above the progress bar (the top of the page holds the app header,
    page titles and most highlight rings): "3 | The hour-long, 8-person call". Slides in, holds 3.5 s, fades."""
    fs = em_to_fs(UI_EM)
    x, y, h, pad, gap = CHIP_X, CHIP_Y, 38, 16, 11
    num = str(step)
    wn, wl = text_width(num, UI_FONT, fs), text_width(label, UI_FONT, fs)
    w = pad + wn + gap + 1.5 + gap + wl + pad + 2
    t0, t1 = t, min(t + 3.5, SHOW_TO - 0.2)
    # leave early (but after at least 2 s) if a highlight ring or its label arrives underneath
    k = t0 + 2.0
    while k < t1 and not (ring_at(k) and covers_ring((x, y, x + w, y + h), ring_at(k))):
        k += 0.05
    t1 = min(t1, k)
    anim = lambda dx, dy=0: f"\\move({dx - 12:.1f},{dy:.1f},{dx:.1f},{dy:.1f},0,320)\\fad(280,450)"
    base = baseline_to_an(y + h / 2 + CAP * UI_EM / 2, fs)
    pill(t0, t1, x, y, w, h, anim, "chip: " + label)
    xs = x + pad + wn + gap
    draw(3, t0, t1, f"m {xs:.1f} {y + 11} l {xs + 1.5:.1f} {y + 11} l {xs + 1.5:.1f} {y + h - 11} l {xs:.1f} {y + h - 11}",
         PAPER, 0xA8, anim(0))
    ev(3, t0, t1, "Chip", f"{{\\an1{anim(x + pad, base)}\\1c{col(LTEAL)}}}{num}")
    ev(3, t0, t1, "Chip", f"{{\\an1{anim(xs + 1.5 + gap, base)}}}{label}")


def fast_badge(a, b, speed):
    """Badge for the whole fast-forwarded range, so nobody mistakes the speed for the real thing. It sits just
    above the presenter bubble, right-aligned with it: clear of the bubble, the subtitles and the app header."""
    fs = em_to_fs(UI_EM)
    label = f"{speed:g}\u00d7 speed \u00b7 waiting for the AI"
    h, pad, icon, gap = 38, 16, 23, 10
    w = pad + icon + gap + text_width(label, UI_FONT, fs) + pad + 2
    x, y = BUBBLE[2] - w, BUBBLE[1] - 14 - h
    anim = lambda dx, dy=0: f"\\pos({dx:.1f},{dy:.1f})\\fad(150,200)"
    pill(a, b, x, y, w, h, anim, f"badge: {speed:g}x")
    cy, ix = y + h / 2, x + pad
    tri = lambda x0: f"m {x0:.1f} {cy - 7:.1f} l {x0 + 11:.1f} {cy:.1f} l {x0:.1f} {cy + 7:.1f}"
    draw(3, a, b, tri(ix) + " " + tri(ix + 12), LTEAL, 0, anim(0))
    ev(3, a, b, "Chip", f"{{\\an1{anim(ix + icon + gap, baseline_to_an(cy + CAP * UI_EM / 2, fs))}}}{label}")


def progress_bar(starts):
    h, gapw = 5, 4
    y0, y1 = H - h, H
    xs = [0] + [round(t / TOTAL * W) for t in starts if 1 < t / TOTAL * W < W - 1] + [W]
    segs = " ".join(f"m {a + (gapw / 2 if a else 0):.1f} {y0} l {b - (gapw / 2 if b < W else 0):.1f} {y0} "
                    f"l {b - (gapw / 2 if b < W else 0):.1f} {y1} l {a + (gapw / 2 if a else 0):.1f} {y1}"
                    for a, b in zip(xs, xs[1:]))
    t0, t1 = SHOW_FROM, SHOW_TO
    fade = "\\fad(300,300)"
    draw(4, t0, t1, segs, LTEAL, 0x90, "\\pos(0,0)" + fade)
    xa, xb = t0 / TOTAL * W, t1 / TOTAL * W
    dur = int(round((t1 - t0) * 1000))
    draw(5, t0, t1, segs, TEAL, 0,
         f"\\pos(0,0){fade}\\clip(0,{y0 - 2},{xa:.1f},{H})\\t(0,{dur},\\clip(0,{y0 - 2},{xb:.1f},{H}))")


def lint_cues(cues):
    """Warns about punctuation that is inconsistent inside a cue (a sentence end without a full stop, or a full
    stop followed by a lower-case word)."""
    proper = set(NAMES.values()) | {"Ask", "Keyword", "Playlists", "I'd", "I’d", "May"}
    for c in cues:
        ws = [w["w"] for w in c["words"]]
        for a, b in zip(ws, ws[1:]):
            b0 = b.strip("\u201c\u201d.,:;?!")
            if (ends_sentence(a) and b0[:1].islower()) or (not ends_sentence(a) and not a.endswith(":")
                                                            and b0[:1].isupper() and b0 not in proper):
                print(f"  punctuation {c['t0']:7.2f} {a!r} {b!r}: {' / '.join(c['lines'])}")


def check_clashes():
    """Warns where a graphic covers an orange highlight ring's outline or its label, overlaps the presenter
    bubble, or (for subtitles) reaches past SUB_MAX_X."""
    bx0, by0, bx1, by1 = BUBBLE
    for t0, t1, x0, y0, x1, y1, what in BOXES:
        if x0 < bx1 and x1 > bx0 and y0 < by1 and y1 > by0:
            print(f"  bubble {t0:7.2f}-{t1:7.2f} {what[:40]!r} overlaps the presenter bubble")
        if what.startswith("subtitle") and x1 > SUB_MAX_X:
            print(f"  wide   {t0:7.2f}-{t1:7.2f} {what[:40]!r} reaches x {x1:.0f} > {SUB_MAX_X}")
        hits, t = set(), t0
        while t < t1:
            if ring_at(t) and covers_ring((x0, y0, x1, y1), ring_at(t)):
                hits.add(ring_at(t)["tag"])
            t += 0.1
        if hits:
            print(f"  clash {t0:7.2f}-{t1:7.2f} {what[:40]!r} with ring(s) {sorted(map(str, hits))}")


def main():
    os.makedirs(OUT, exist_ok=True)
    words = build_words()
    cues = make_cues(words)
    lint_cues(cues)
    subtitles(cues)
    chaps = chapters()
    for t, step, label in chaps:
        if SHOW_FROM <= t < SHOW_TO:
            chip(t, step, label)
    speeds = E.get("fast_speed") or [E["ff_speed"]] * len(E["fast"])  # per-range speed when the edit has it
    for (a, b), speed in zip(E["fast"], speeds):
        fast_badge(a, b, speed)
    progress_bar([t for t, step, label in chaps])
    check_clashes()

    sub_fs, ui_fs = em_to_fs(SUB_EM), em_to_fs(UI_EM)
    head = f"""[Script Info]
Title: Sounding walkthrough graphics
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{SUB_FONT},{sub_fs},{col(PAPER)},{col(PAPER)},{col(INK)},{col(INK, 0xFF)},0,0,0,0,100,100,0.2,0,1,0,0,2,0,0,0,1
Style: Chip,{UI_FONT},{ui_fs},{col(PAPER)},{col(PAPER)},{col(INK)},{col(INK, 0xFF)},0,0,0,0,100,100,0.3,0,1,0,0,1,0,0,0,1
Style: Draw,{UI_FONT},20,{col(PAPER)},{col(PAPER)},{col(INK)},{col(INK, 0xFF)},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(f"{OUT}/overlay.ass", "w") as f:
        f.write(head + "\n".join(events) + "\n")

    def srt_ts(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    with open(f"{OUT}/subtitles.srt", "w") as f:
        for n, c in enumerate(cues, 1):
            f.write(f"{n}\n{srt_ts(c['t0'])} --> {srt_ts(c['t1'])}\n" + "\n".join(c["lines"]) + "\n\n")
    json.dump([dict(t0=c["t0"], t1=c["t1"], lines=c["lines"],
                    words=[(w["w"], w["asr"], w["src"], w["t0"], w["t1"]) for w in c["words"]]) for c in cues],
              open(f"{OUT}/cues.json", "w"), indent=1, ensure_ascii=False)
    vf = f"subtitles=filename={OUT}/overlay.ass:fontsdir={FONTS}"
    open(f"{OUT}/filter.txt", "w").write(vf + "\n")
    print(f"{len(cues)} cues, {len(events)} events -> {OUT}")
    print("filter:", vf)


if __name__ == "__main__":
    main()
