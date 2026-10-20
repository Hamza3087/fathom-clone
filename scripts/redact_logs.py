#!/usr/bin/env python3
"""Redacts exported agent logs before they're published.
Removes credentials, personal contact details, and details of unrelated production infrastructure
(the session ran on a machine that also manages other servers). Safe to run repeatedly.
Usage: redact_logs.py <dir-or-file> ..."""
import json, os, re, sys

KEEP_IPS = {"5.9.117.80", "127.0.0.1", "0.0.0.0"}            # the public demo host and loopback
KEEP_EMAIL = re.compile(r"@(sounding\.demo|example\.com|icsi\.example|realreaction\.example|northwind\.com|acme\.io|anthropic\.com)$", re.I)
SUBS = [
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}"), "[redacted github token]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "hf_[redacted]"),
    (re.compile(r"snd_[A-Za-z0-9_-]{20,}"), "snd_[redacted]"),
    (re.compile(r"(SOUNDING_(?:PASSWORD|SECRET)=)\S+"), r"\1[redacted]"),
    (re.compile(r"sounding_session=[A-Za-z0-9_-]{20,}"), "sounding_session=[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\"?\s*[:=]\s*\"?)(?!\[redacted)[^\s\"',}]{8,}"), r"\1\2[redacted]"),
    (re.compile(r"\b[A-Za-z0-9_-]*(?:hetzner|_key\d?|id_rsa|id_ed25519)[A-Za-z0-9_-]*\b"), "[ssh-key]"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Lines about unrelated systems on the operator's machine are dropped entirely. The patterns live in a private,
# uncommitted file (one regex per line) so the published repo doesn't describe that infrastructure either.
PRIVATE = os.path.expanduser("~/.config/sounding/redact-private.txt")
_pats = [l.strip() for l in open(PRIVATE) if l.strip() and not l.startswith("#")] if os.path.exists(PRIVATE) else []
SENSITIVE = re.compile("|".join(f"(?:{p})" for p in _pats)) if _pats else None


def clean(text):
    for p, r in SUBS:
        text = p.sub(r, text)
    text = EMAIL.sub(lambda m: m.group(0) if KEEP_EMAIL.search(m.group(0)) else "[email]", text)
    text = IPV4.sub(lambda m: m.group(0) if m.group(0) in KEEP_IPS or m.group(0).startswith(("10.", "192.168.")) else "[ip]", text)
    if SENSITIVE is None:
        return text
    return "\n".join("[redacted: detail about unrelated infrastructure]" if SENSITIVE.search(l) else l for l in text.split("\n"))


def clean_obj(o):
    if isinstance(o, str): return clean(o)
    if isinstance(o, list): return [clean_obj(x) for x in o]
    if isinstance(o, dict): return {k: clean_obj(v) for k, v in o.items()}
    return o


def redact_file(path):
    if path.endswith(".jsonl"):
        out = [json.dumps(clean_obj(json.loads(l)), ensure_ascii=False) for l in open(path) if l.strip()]
        open(path, "w").write("\n".join(out) + "\n")
    elif path.endswith(".md"):
        open(path, "w").write(clean(open(path).read()))


if __name__ == "__main__":
    for target in sys.argv[1:]:
        files = [os.path.join(d, f) for d, _, fs in os.walk(target) for f in fs] if os.path.isdir(target) else [target]
        for f in files:
            redact_file(f)
