#!/usr/bin/env python3
"""Exports the Claude Code session that built this repo into .agent-logs/.

Writes, per session (and per sub-agent the session launched):
  .agent-logs/<name>.jsonl  one record per prompt, response, tool call and tool result
  .agent-logs/<name>.md     the same thing, readable

Run it at each milestone and commit the result:  python3 scripts/capture_agent_logs.py <session-id>
Secrets are redacted before anything is written.
"""
import json, os, re, sys, glob

PROJECTS = os.path.expanduser("~/.claude/projects/-root")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".agent-logs")
MAX_RESULT = 4000

REDACT = [
    (re.compile(r"(SOUNDING_(?:PASSWORD|SECRET)=)\S+"), r"\1[redacted]"),
    (re.compile(r"snd_[A-Za-z0-9_-]{20,}"), "snd_[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\"?\s*[:=]\s*\"?)(?!\[redacted)[^\s\"',}]{8,}"), r"\1\2[redacted]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "hf_[redacted]"),
    (re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{20,}\b"), "[redacted key]"),
    (re.compile(r"sounding_session=[A-Za-z0-9_-]{20,}"), "sounding_session=[redacted]"),
]
SYSTEM_BLOCK = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def clean(s):
    s = SYSTEM_BLOCK.sub("", s)
    for p, r in REDACT:
        s = p.sub(r, s)
    return s.strip()


def clean_obj(o):
    if isinstance(o, str):
        return clean(o)
    if isinstance(o, list):
        return [clean_obj(x) for x in o]
    if isinstance(o, dict):
        return {k: clean_obj(v) for k, v in o.items()}
    return o


def records(path):
    for line in open(path):
        d = json.loads(line)
        t, ts = d.get("type"), d.get("timestamp")
        if t not in ("user", "assistant"):
            continue
        content = d["message"]["content"]
        if isinstance(content, str):
            text = clean(content)
            if text:
                yield dict(ts=ts, role=t, kind="text", text=text)
            continue
        for c in content:
            if c.get("type") == "text" and clean(c["text"]):
                yield dict(ts=ts, role=t, kind="text", text=clean(c["text"]))
            elif c.get("type") == "tool_use":
                yield dict(ts=ts, role=t, kind="tool_call", tool=c["name"], input=clean_obj(c["input"]))
            elif c.get("type") == "tool_result":
                body = c.get("content")
                if isinstance(body, list):
                    body = "\n".join(x.get("text", "[image]") if x.get("type") == "text" else "[image]" for x in body)
                body = clean(str(body or ""))
                yield dict(ts=ts, role=t, kind="tool_result", text=body[:MAX_RESULT] + (" …[truncated]" if len(body) > MAX_RESULT else ""))


def export(path, name):
    recs = list(records(path))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.jsonl"), "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT, f"{name}.md"), "w") as f:
        f.write(f"# Agent log: {name}\n\n")
        for r in recs:
            if r["kind"] == "text":
                who = "User" if r["role"] == "user" else "Agent"
                f.write(f"### {who} · {r['ts']}\n\n{r['text']}\n\n")
            elif r["kind"] == "tool_call":
                f.write(f"**Tool call: {r['tool']}**\n\n```json\n{json.dumps(r['input'], indent=1, ensure_ascii=False)[:MAX_RESULT]}\n```\n\n")
            else:
                f.write(f"<details><summary>Tool result</summary>\n\n```\n{r['text']}\n```\n</details>\n\n")
    return len(recs)


def redact_all():
    """Second pass with the publication redactor (tokens, personal details, unrelated infrastructure)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("redact_logs", os.path.join(os.path.dirname(os.path.abspath(__file__)), "redact_logs.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    for f in glob.glob(os.path.join(OUT, "*")):
        mod.redact_file(f)


if __name__ == "__main__":
    sid = sys.argv[1]
    n = export(os.path.join(PROJECTS, f"{sid}.jsonl"), f"session-{sid[:8]}")
    print(f"session-{sid[:8]}: {n} records")
    for sub in sorted(glob.glob(os.path.join(PROJECTS, sid, "subagents", "agent-*.jsonl"))):
        meta = sub.replace(".jsonl", ".meta.json")
        label = os.path.basename(sub)[:-6]
        if os.path.exists(meta):
            label = json.load(open(meta)).get("name") or label
        n = export(sub, f"session-{sid[:8]}-subagent-{label}")
        print(f"  subagent {label}: {n} records")
    redact_all()
