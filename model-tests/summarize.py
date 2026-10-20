import json, time, glob, re, urllib.request, xml.etree.ElementTree as ET, os, zipfile
S = "/root/sounding"
SYS = ("You write meeting notes from a speaker-labelled transcript. The transcript comes from speech recognition and has errors. "
       "Return only JSON with keys: title (short), overview (2-3 sentences), key_points (3-6 strings), decisions (strings), "
       "action_items (objects with text, owner, timestamp_seconds). Use speaker labels as owners. Do not invent facts.")
def call(msgs, max_tokens=1500):
    body = json.dumps(dict(model="sounding-qwen", messages=msgs, temperature=0.2, max_tokens=max_tokens,
                           response_format={"type": "json_object"}, chat_template_kwargs={"enable_thinking": False})).encode()
    r = urllib.request.Request("http://127.0.0.1:8011/v1/chat/completions", body, {"Content-Type": "application/json"})
    j = json.load(urllib.request.urlopen(r, timeout=600)); return j["choices"][0]["message"]["content"], j["usage"]
z = zipfile.ZipFile(S + "/data/ami_manual.zip")
def human_summary(mid):
    try:
        root = ET.fromstring(z.read(f"abstractive/{mid}.abssumm.xml"))
        return {sec.tag: [s.text for s in sec] for sec in root}
    except KeyError: return None
out = {}
for f in sorted(glob.glob(S + "/results/*.turns.json")):
    mid = os.path.basename(f).split(".")[0]
    turns = json.load(open(f))
    tx = "\n".join(f"[{int(t['start'])//60}:{int(t['start'])%60:02d}] {t['speaker']}: {t['text']}" for t in turns)
    t0 = time.time(); raw, use = call([{"role": "system", "content": SYS}, {"role": "user", "content": tx}])
    el = time.time() - t0
    try: js = json.loads(raw); ok = all(k in js for k in ["overview", "key_points", "decisions", "action_items"])
    except Exception: js, ok = None, False
    q = "What did the group decide, and who disagreed? Cite timestamps."
    t1 = time.time(); ans, _ = call([{"role": "system", "content": "Answer questions about this meeting transcript briefly, citing [m:ss] timestamps. Reply as JSON {\"answer\": str}."},
                                     {"role": "user", "content": tx + "\n\nQuestion: " + q}], 400)
    out[mid] = dict(prompt_tokens=use["prompt_tokens"], summary_sec=round(el, 1), ask_sec=round(time.time() - t1, 1),
                    valid_json=ok, summary=js, ask=ans, human=human_summary(mid))
    print(f"{mid}: {use['prompt_tokens']} tokens in, summary {el:.1f}s, valid JSON {ok}, ask {out[mid]['ask_sec']}s", flush=True)
json.dump(out, open(S + "/results/summaries.json", "w"), indent=1)
