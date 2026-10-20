"""Everything that talks to Qwen (vLLM, OpenAI-compatible, 32k context)."""
import json, math, re, urllib.request
from collections import Counter

QWEN = "http://127.0.0.1:8011/v1/chat/completions"
MAX_CHARS = 90000  # roughly 24k tokens, leaves room in the 32k context

TEMPLATES = {
    "general": dict(name="General", sections=["Key takeaways", "Topics discussed", "Decisions", "Next steps"],
                    guide="A general-purpose summary for any meeting."),
    "sales": dict(name="Sales discovery (SPICED)", sections=["Situation", "Pain", "Impact", "Critical event", "Decision process", "Next steps"],
                  guide="A sales call. Capture the prospect's situation, pain, business impact, deadlines and how they buy."),
    "customer_success": dict(name="Customer success", sections=["Account health", "Value delivered", "Risks and blockers", "Feature requests", "Next steps"],
                             guide="A call with an existing customer. Flag churn risk and expansion signals."),
    "one_on_one": dict(name="1:1", sections=["Updates", "Challenges", "Feedback", "Goals and growth", "Next steps"],
                       guide="A manager and report 1:1."),
    "interview": dict(name="Candidate interview", sections=["Background", "Strengths", "Concerns", "Motivation", "Recommendation"],
                      guide="A job interview. Be fair and evidence-based; quote what the candidate said."),
    "project": dict(name="Project sync", sections=["Progress", "Plans", "Blockers", "Decisions"],
                    guide="An internal project or stand-up meeting."),
}


def qwen(system, user, max_tokens=1500, as_json=True, temperature=0.2):
    body = dict(model="sounding-qwen", temperature=temperature, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                chat_template_kwargs={"enable_thinking": False})
    if as_json:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(QWEN, json.dumps(body).encode(), {"Content-Type": "application/json"})
    out = json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]["content"]
    if not as_json:
        return out
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", out, re.S)
        return json.loads(m.group(0)) if m else {}


def clock(t):
    t = int(t or 0)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def stamp(v, dur):
    m = re.fullmatch(r"\[?(?:(\d+):)?(\d+):(\d{2})\]?", str(v or "").strip())
    if not m:
        return None
    t = int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3])
    return float(t) if t <= (dur or 1e9) else None


def transcript_text(turns, names=None):
    names = names or {}
    def who(label):
        return f"{label} ({names[label]})" if names.get(label) else label
    return "\n".join(f"[{clock(t['t'])}] {who(t['s'])}: {t['x']}" for t in turns)


def split(text, limit=MAX_CHARS):
    parts, cur = [], ""
    for line in text.splitlines(True):
        if len(cur) + len(line) > limit and cur:
            parts.append(cur); cur = ""
        cur += line
    return parts + [cur] if cur else parts


# ---------- notes ----------
def template_spec(key, custom=None):
    if custom:
        base = TEMPLATES.get(custom["base"], TEMPLATES["general"])
        return dict(name=custom["name"], sections=base["sections"], guide=base["guide"] + " " + custom["instructions"])
    return TEMPLATES.get(key, TEMPLATES["general"])


def notes_prompt(spec, extra, dictionary):
    s = ("You write meeting notes from a speaker-labelled transcript produced by speech recognition, so expect some wrong words. "
         f"{spec['guide']} Return only JSON with keys: title (under 8 words), overview (2-3 plain sentences), "
         f"sections (a list, in this order, of objects with title and bullets, using exactly these section titles: {json.dumps(spec['sections'])}; "
         "each bullet is an object with text and at, where at is the [m:ss] stamp of the line it comes from, copied from the transcript; "
         "leave bullets empty for a section the meeting didn't cover), "
         "actions (objects with text, owner, at: owner is a speaker label exactly as written), "
         "names (object mapping a speaker label to a person's name or role, only when the transcript makes it clear). "
         "Do not invent facts. Write plainly.")
    if dictionary:
        s += (f" Company vocabulary that may appear in the transcript (spell it exactly like this; these are words, not people): "
              f"{', '.join(dictionary)}.")
    if extra:
        s += f" Additional instructions from the user: {extra}"
    return s


def notes(turns, names, spec, extra="", dictionary=()):
    sys_p = notes_prompt(spec, extra, dictionary)
    parts = split(transcript_text(turns, names))
    if len(parts) == 1:
        return qwen(sys_p, parts[0], max_tokens=2500)
    partial = [json.dumps(qwen(sys_p, f"Part {i + 1} of {len(parts)}:\n{p}", max_tokens=2500)) for i, p in enumerate(parts)]
    return qwen(sys_p + " You are given notes for consecutive parts of one meeting; merge them into one set of notes.",
                "\n\n".join(partial), max_tokens=2500)


LABEL = re.compile(r"^\s*(Speaker \d+)")


def label_of(v, speakers):
    """Qwen sometimes echoes "Speaker 2 (Dana)"; keep just the label if it's one of ours."""
    m = LABEL.match(str(v or ""))
    return m.group(1) if m and m.group(1) in speakers else None


def clean_notes(n, speakers, dur, spec):
    n = n or {}
    sections = []
    raw = n.get("sections") or []
    if isinstance(raw, dict):  # {"Title": [bullets]} instead of [{"title", "bullets"}]
        raw = [dict(title=k, bullets=v) for k, v in raw.items()]
    got = {str(s.get("title", "")).strip().lower(): s for s in raw if isinstance(s, dict)}
    for title in spec["sections"]:
        s = got.get(title.lower(), {})
        bullets = []
        for b in s.get("bullets") or []:
            if isinstance(b, str):
                bullets.append(dict(text=b, t=None))
            elif isinstance(b, dict) and b.get("text"):
                bullets.append(dict(text=str(b["text"]), t=stamp(b.get("at"), dur)))
        sections.append(dict(title=title, bullets=bullets))
    actions = []
    for i, a in enumerate(n.get("actions") or []):
        if isinstance(a, dict) and a.get("text"):
            actions.append(dict(id=f"a{i}", text=str(a["text"]), speaker=label_of(a.get("owner"), speakers),
                                assignee=None, t=stamp(a.get("at"), dur), done=False))
    generic = {"team member", "participant", "speaker", "unknown", "attendee"}
    names = {label_of(k, speakers): str(v).strip() for k, v in (n.get("names") or {}).items()
             if label_of(k, speakers) and v and str(v).strip().lower() not in generic}
    return dict(title=str(n.get("title") or "Untitled meeting"), overview=str(n.get("overview") or ""),
                sections=sections, actions=actions, names=names)


def classify(turns, types):
    """Picks the org meeting type that fits best, or None."""
    if not types:
        return None
    opts = "\n".join(f"- {t['id']}: {t['name']}: {t['description']}" for t in types)
    r = qwen("Classify the meeting into one of the meeting types, or none if nothing fits. "
             'Reply as JSON {"type": id or null}.', f"Meeting types:\n{opts}\n\nTranscript start:\n{transcript_text(turns)[:12000]}",
             max_tokens=60)
    ids = {t["id"] for t in types}
    return r.get("type") if r.get("type") in ids else None


# ---------- trackers & scorecards ----------
def find_tracker_hits(turns, trackers, dur):
    """AI trackers: finds moments matching each tracker's description by meaning, not exact words."""
    if not trackers:
        return []
    spec = "\n".join(f"- {t['id']}: {t['name']}: {t['terms'].get('description', '')}" for t in trackers)
    hits = []
    for part in split(transcript_text(turns), 60000):
        r = qwen("Find moments in the transcript that match each tracker, by meaning rather than exact words. Only include clear matches. "
                 'Reply as JSON {"hits": [{"tracker": tracker id, "at": "[m:ss] stamp copied from the line", "quote": "short quote"}]}.',
                 f"Trackers:\n{spec}\n\nTranscript:\n{part}", max_tokens=1500)
        ids = {t["id"] for t in trackers}
        for h in r.get("hits") or []:
            t = stamp(h.get("at"), dur)
            if h.get("tracker") in ids and t is not None:
                hits.append((h["tracker"], t, str(h.get("quote", ""))[:300]))
    return hits


def score(turns, names, criteria, rep_label, dur):
    who = f" Score the performance of {rep_label}." if rep_label else " Score the meeting as a whole."
    crit = "\n".join(f"- {c['name']}: {c.get('description', '')}" for c in criteria)
    r = qwen("You are a fair coach scoring a recorded meeting against a scorecard." + who +
             ' Reply as JSON {"criteria": [{"name": criterion name, "score": 0-10, "reason": one sentence, "at": "[m:ss] stamp of the best evidence"}], '
             '"feedback": two sentences of coaching}.', f"Scorecard:\n{crit}\n\nTranscript:\n{transcript_text(turns, names)[:MAX_CHARS]}",
             max_tokens=1500)
    out = []
    names_ok = {c["name"] for c in criteria}
    for c in r.get("criteria") or []:
        if c.get("name") in names_ok:
            s = max(0.0, min(10.0, float(c.get("score") or 0)))
            out.append(dict(name=c["name"], score=s, reason=str(c.get("reason", "")), t=stamp(c.get("at"), dur)))
    total = round(sum(c["score"] for c in out) / len(out) * 10, 0) if out else 0
    return total, dict(criteria=out, feedback=str(r.get("feedback", "")))


# ---------- follow-ups, translation, ask ----------
def followup(m, user, org_name):
    s = m.get("summary") or {}
    notes_txt = json.dumps({"overview": s.get("overview"), "sections": s.get("sections"), "actions": [a["text"] for a in m["actions"]]})
    return qwen(f"Draft a short, friendly follow-up email from {user['name']} at {org_name} to the other attendees of this meeting. "
                "Thank them, recap what was agreed, list next steps with owners, and close. No placeholders in square brackets. "
                'Reply as JSON {"subject": string, "body": plain text with line breaks}.',
                f"Meeting: {m['title']}\nNotes: {notes_txt}", max_tokens=900, temperature=0.4)


LANGS = ["Spanish", "Portuguese", "German", "French", "Italian", "Dutch", "Arabic", "Urdu", "Hindi", "Chinese", "Japanese"]


def translate(summary, actions, lang):
    payload = dict(overview=summary.get("overview", ""), sections=[dict(title=s["title"], bullets=[b["text"] for b in s["bullets"]])
                                                                     for s in summary.get("sections", [])],
                   actions=[a["text"] for a in actions])
    r = qwen(f"Translate every string value of this JSON into {lang}. Keep the structure and the number of items exactly. Reply with the JSON only.",
             json.dumps(payload, ensure_ascii=False), max_tokens=3000)
    out = dict(overview=str(r.get("overview", "")), sections=[], actions=[str(x) for x in r.get("actions") or []])
    for s, ts in zip(summary.get("sections", []), r.get("sections") or []):
        bullets = [dict(text=str(x), t=b["t"]) for b, x in zip(s["bullets"], ts.get("bullets") or [])]
        out["sections"].append(dict(title=str(ts.get("title") or s["title"]), bullets=bullets))
    return out


CITE = re.compile(r"\[(?:M\d+ ?)?\d+:\d{2}(?::\d{2})?\]")
RECITE = (" Your previous answer had no timestamps. Every factual sentence must end with the [m:ss] stamp of the transcript line "
          "it comes from, copied exactly as it appears in the transcript.")


def ask_meeting(m, question, user):
    tx = transcript_text(m["transcript"], m.get("names"))[:MAX_CHARS]
    system = (f"You answer questions about one meeting transcript for {user['name']}. Be brief and plain. After each claim cite the moment as [m:ss]. "
              "If the transcript doesn't answer the question, say so. If asked to draft something, write it from the user. "
              'Reply as JSON {"answer": string}.')
    prompt = f"Meeting: {m['title']} ({m['date'][:10]})\n{tx}\n\nQuestion: {question}"
    answer = str(qwen(system, prompt, max_tokens=700).get("answer", ""))
    # The model sometimes drops citations; answers are only useful if they link to the moment, so ask once more.
    if not CITE.search(answer) and "doesn't" not in answer and "not mention" not in answer:
        answer = str(qwen(system + RECITE, prompt, max_tokens=700).get("answer", "")) or answer
    if not CITE.search(answer):
        answer = add_citations(answer, m["transcript"])
    return answer


def add_citations(answer, turns, window=3):
    """Last resort when the model won't cite: attach to each sentence the moment whose words it overlaps most."""
    if not turns:
        return answer
    chunks = []
    for i in range(len(turns)):
        text = " ".join(t["x"] for t in turns[i:i + window])
        chunks.append((turns[i]["t"], set(_toks(text))))
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", answer.strip()):
        words = set(_toks(sent))
        best, score = None, 0.0
        for t, toks in chunks:
            if not toks: continue
            sc = len(words & toks) / (len(words) ** 0.5 + 1)
            if sc > score:
                best, score = t, sc
        out.append(f"{sent.rstrip()} [{clock(best)}]" if best is not None and score >= 1.2 and len(words) >= 4 else sent)
    return " ".join(out)


WORD = re.compile(r"[a-z0-9']+")
STOP = set("the a an and or of to in on for is are was were be been it that this with about from how what who when did do does we you i they he she "
           "at as by our your their there here so if but not can could would should will just have has had me my us them any all".split())


def _toks(s):
    return [w for w in WORD.findall(s.lower()) if w not in STOP and len(w) > 1]


def chunks_for(m, size=700):
    names = m.get("names") or {}
    out, cur, start = [], "", None
    for t in m["transcript"]:
        line = f"{names.get(t['s'], t['s'])}: {t['x']}\n"
        if start is None: start = t["t"]
        cur += line
        if len(cur) >= size:
            out.append((start, cur)); cur, start = "", None
    if cur:
        out.append((start or 0, cur))
    return out


def ask_all(meetings, question, user):
    """Account-wide Ask: BM25 over transcript chunks of the meetings the user can see, then Qwen answers with citations."""
    refs = {f"M{i + 1}": m for i, m in enumerate(meetings)}
    docs = []
    for ref, m in refs.items():
        for t, text in chunks_for(m):
            docs.append((ref, t, text, _toks(text)))
    q = _toks(question)
    N = len(docs) or 1
    df = Counter(w for d in docs for w in set(d[3]))
    avg = sum(len(d[3]) for d in docs) / N if docs else 1
    def bm25(d):
        tf = Counter(d[3]); s = 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(d[3]) / avg))
        return s
    ranked = sorted((d for d in docs if q), key=bm25, reverse=True)
    catalog = "\n".join(f"{ref}: {m['title']} ({m['date'][:10]}, owner {m.get('owner_name', '')}) — {(m.get('summary') or {}).get('overview', '')[:300]}"
                        for ref, m in list(refs.items())[:80])
    budget, ctx = MAX_CHARS - len(catalog) - 2000, []
    for ref, t, text, _ in ranked[:60]:
        piece = f"[{ref} {clock(t)}]\n{text}"
        if budget - len(piece) < 0: break
        ctx.append(piece); budget -= len(piece)
    r = qwen(f"You answer questions across many meetings for {user['name']}. Use the meeting list and the transcript excerpts. "
             "Be brief and plain. Cite every claim as [M3 12:34] using the meeting reference and time shown on the excerpt, "
             "or [M3] when citing the meeting list. If the material doesn't answer the question, say so. "
             'Reply as JSON {"answer": string}.',
             f"Meetings:\n{catalog}\n\nExcerpts:\n" + "\n\n".join(ctx) + f"\n\nQuestion: {question}", max_tokens=900)
    if not re.search(r"\[M\d+", r.get("answer", "")):
        r = qwen(f"You answer questions across many meetings for {user['name']}. Cite every claim as [M3 12:34] using the meeting "
                 "reference and time on the excerpt, or [M3] for the meeting list. Answers without citations are rejected. "
                 'Reply as JSON {"answer": string}.',
                 f"Meetings:\n{catalog}\n\nExcerpts:\n" + "\n\n".join(ctx) + f"\n\nQuestion: {question}", max_tokens=900) or r
    answer = str(r.get("answer", ""))
    if not re.search(r"\[M\d+", answer) and ranked:
        # still no citations: attach each sentence to the excerpt it overlaps most
        out = []
        for sent in re.split(r"(?<=[.!?])\s+", answer.strip()):
            words = set(_toks(sent))
            best = max(ranked[:60], key=lambda d: len(words & set(d[3])), default=None)
            hit = best and len(words & set(best[3])) / (len(words) ** 0.5 + 1)
            out.append(f"{sent.rstrip()} [{best[0]} {clock(best[1])}]" if best and hit >= 1.2 and len(words) >= 4 else sent)
        r = {"answer": " ".join(out)}
    used = set(re.findall(r"\[(M\d+)", r.get("answer", "")))
    return str(r.get("answer", "")), {k: dict(id=v["id"], title=v["title"], date=v["date"]) for k, v in refs.items() if k in used}
