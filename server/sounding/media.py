"""Audio/video handling and the speech models: Sortformer (who spoke) + Parakeet (what was said)."""
import json, logging, os, re, subprocess

log = logging.getLogger("sounding")
MODELS = os.path.join(os.environ.get("SOUNDING_ROOT", "/root/sounding"), "models")
_models = {}


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def probe(p):
    out = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                                              "-of", "json", p]))
    kinds = {s.get("codec_type") for s in out.get("streams", [])}
    return float(out["format"].get("duration") or 0), "video" in kinds, "audio" in kinds


def prepare(src, work):
    """Makes audio16k.wav for the models, audio.mp3 for playback, and video.mp4 when the upload has video."""
    dur, has_video, has_audio = probe(src)
    if not has_audio:
        raise ValueError("That file has no audio track.")
    ffmpeg("-i", src, "-vn", "-ac", "1", "-ar", "16000", f"{work}/audio16k.wav")
    ffmpeg("-i", f"{work}/audio16k.wav", "-c:a", "libmp3lame", "-b:a", "48k", f"{work}/audio.mp3")
    if has_video:
        ffmpeg("-i", src, "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", f"{work}/video.mp4")
    return probe(f"{work}/audio16k.wav")[0], has_video


def models():
    """Per-process model cache. Only the speech child process (sounding.speech) calls this."""
    if "torch" not in _models:
        import torch
        _models["torch"] = torch
    return _models


def load(kind):
    m = models()
    if kind not in m:
        from nemo.collections.asr.models import SortformerEncLabelModel, ASRModel
        logging.disable(logging.WARNING)
        if kind == "diar":
            d = SortformerEncLabelModel.restore_from(MODELS + "/diar_streaming_sortformer_4spk-v2.nemo", map_location="cuda").eval()
            sm = d.sortformer_modules  # model card "high latency" streaming config
            sm.chunk_len, sm.chunk_right_context, sm.fifo_len, sm.spkcache_update_period, sm.spkcache_len = 340, 40, 40, 300, 188
            m["diar"] = d
        else:
            a = ASRModel.restore_from(MODELS + "/parakeet-tdt-0.6b-v2.nemo", map_location="cuda").eval()
            a.change_attention_model("rel_pos_local_attn", [256, 256]); a.change_subsampling_conv_chunking_factor(1)
            m["asr"] = a
        logging.disable(logging.NOTSET)
    return m[kind]


def unload(kind):
    """Frees a model's GPU memory before the next stage loads its own."""
    import gc
    m = models()
    m.pop(kind, None)
    gc.collect()
    m["torch"].cuda.empty_cache()


def diarize(wav):
    d, torch = load("diar"), models()["torch"]
    try:
        out = d.diarize(audio=wav, batch_size=1)[0]
    except torch.OutOfMemoryError:
        # Long recordings can outgrow the GPU memory Qwen leaves free; the CPU is slower but fine.
        torch.cuda.empty_cache(); log.info("diarization out of GPU memory, retrying on CPU")
        d.to("cpu")
        try:
            out = d.diarize(audio=wav, batch_size=1)[0]
        finally:
            d.to("cuda"); torch.cuda.empty_cache()
    segs = []
    for s in out:
        a, b, spk = s.split(); segs.append((float(a), float(b), spk))
    torch.cuda.empty_cache()
    return sorted(segs)


CLUSTER_MIN_SECONDS = 300  # short calls rarely have more than 4 speakers; skip the extra pass


def speech_regions(segs, gap=0.3):
    out = []
    for a, b, _ in sorted(segs):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def cluster_speakers(wav, segs, work):
    """Sortformer tracks at most 4 speakers. For bigger calls, keep its speech regions but re-assign speakers
    by clustering TitaNet voice embeddings, which has no cap (tested on 8-9 person ICSI meetings: speaker
    confusion fell from 15-21% to 5-8%)."""
    from .cluster import cluster
    out = f"{work}/diar"; os.makedirs(out, exist_ok=True)
    try:
        return sorted(tuple(x) for x in cluster(wav, speech_regions(segs), out, os.path.join(MODELS, "titanet_large.nemo")))
    finally:
        import shutil; shutil.rmtree(out, ignore_errors=True)


def diarize_meeting(wav, dur, work):
    """Sortformer first (best with overlapping talk); switch to clustering when the call has more than 4 voices."""
    segs = diarize(wav)
    unload("diar")
    if dur < CLUSTER_MIN_SECONDS:
        return segs, "sortformer"
    try:
        clustered = cluster_speakers(wav, segs, work)
    except Exception as e:
        log.info("speaker clustering failed (%s); keeping Sortformer speakers", str(e)[:200])
        clustered = []
    models()["torch"].cuda.empty_cache()
    n = len({s for *_, s in clustered})
    log.info("sortformer found %d speakers, clustering found %d", len({s for *_, s in segs}), n)
    return (clustered, "clustering") if n > 4 else (segs, "sortformer")


def speech(wav, dur, work):
    """The whole GPU stage for one recording. Runs inside a child process (see run_speech)."""
    import time as _t
    t0 = _t.time(); segs, diarizer = diarize_meeting(wav, dur, work)
    t1 = _t.time(); words = transcribe(wav, segs, dur, work)
    unload("asr")
    return dict(segs=segs, words=words, diarizer=diarizer, timings=dict(speakers=round(t1 - t0, 1), transcribe=round(_t.time() - t1, 1)))


def run_speech(wav, dur, work):
    """Runs speech() in a fresh process so every byte of GPU memory it used is released when it exits.
    The API process never touches CUDA, which leaves the GPU to Qwen between recordings."""
    import sys as _sys
    out = f"{work}/speech.json"
    r = subprocess.run([_sys.executable, "-m", "sounding.speech", wav, str(dur), work],
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), capture_output=True, text=True, timeout=3 * 3600)
    for line in (r.stderr or "").splitlines():
        if "speakers" in line or "memory" in line:
            log.info("speech: %s", line.strip()[-200:])
    if r.returncode != 0 or not os.path.exists(out):
        err = [l for l in (r.stderr or "").strip().splitlines() if "Error" in l][-1:] or ["speech stage failed"]
        raise RuntimeError(err[0][-300:])
    res = json.load(open(out)); os.remove(out)
    return [tuple(x) for x in res["segs"]], [tuple(x) for x in res["words"]], res["diarizer"], res["timings"]


def cut_points(segs, dur, target=150.0):
    """Split long audio for ASR at silences between speaker segments, about every 2.5 minutes."""
    gaps, end = [], 0.0
    for a, b, _ in segs:
        if a > end + 0.3: gaps.append((end + a) / 2)
        end = max(end, b)
    cuts, pos = [0.0], 0.0
    while dur - pos > target * 1.3:
        want = pos + target
        near = [g for g in gaps if pos + 30 < g < pos + target * 1.25]
        pos = min(near, key=lambda g: abs(g - want)) if near else want
        cuts.append(pos)
    return cuts + [dur]


def asr_span(wav, a, b, work, depth=0):
    asr, torch = load("asr"), models()["torch"]
    part = f"{work}/part.wav"; ffmpeg("-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", wav, "-ac", "1", "-ar", "16000", part)
    try:
        with torch.autocast("cuda", dtype=torch.bfloat16):
            o = asr.transcribe([part], timestamps=True, batch_size=1, verbose=False)[0]
        return [(w["word"], w["start"] + a, w["end"] + a) for w in o.timestamp["word"]]
    except torch.OutOfMemoryError:
        torch.cuda.empty_cache()
        if depth >= 3 or b - a < 20: raise
        mid = (a + b) / 2  # halve the span and try again
        return asr_span(wav, a, mid, work, depth + 1) + asr_span(wav, mid, b, work, depth + 1)
    finally:
        if os.path.exists(part): os.remove(part)
        torch.cuda.empty_cache()


def transcribe(wav, segs, dur, work):
    cuts = cut_points(segs, dur); words = []
    for a, b in zip(cuts, cuts[1:]):
        words += asr_span(wav, a, b, work)
    return words


def to_turns(words, segs):
    names, turns = {}, []
    last = "speaker_0"  # raw diarizer label of the previous word
    for w, a, b in words:
        best, ov = None, -1e9
        for s0, s1, spk in segs:
            if s1 < a - 1 or s0 > b + 1: continue
            o = min(b, s1) - max(a, s0)
            if o > ov: best, ov = spk, o
        last = best or last  # words outside every segment stay with the previous speaker
        spk = names.setdefault(last, f"Speaker {len(names) + 1}")
        if turns and turns[-1]["s"] == spk and a - turns[-1]["end"] < 1.5:
            turns[-1]["x"] += " " + w; turns[-1]["end"] = round(b, 2)
        else:
            turns.append(dict(t=round(a, 2), end=round(b, 2), s=spk, x=w))
    return turns


def apply_dictionary(turns, terms):
    """Fix the casing/spelling of dictionary terms the recognizer got nearly right (case and spacing only)."""
    if not terms:
        return turns
    pats = [(re.compile(r"\b" + r"[\s-]*".join(map(re.escape, t.replace("-", " ").split())) + r"\b", re.I), t) for t in terms]
    for tr in turns:
        for p, t in pats:
            tr["x"] = p.sub(t, tr["x"])
    return turns


def metrics(turns, dur):
    """Talk-time coaching metrics per speaker."""
    per = {}
    runs = []  # consecutive speech by one speaker, for monologues
    for t in turns:
        p = per.setdefault(t["s"], dict(talk=0.0, turns=0, words=0, questions=0, longest=0.0))
        p["talk"] += max(0, t["end"] - t["t"]); p["turns"] += 1; p["words"] += len(t["x"].split())
        p["questions"] += t["x"].count("?")
        if runs and runs[-1][0] == t["s"] and t["t"] - runs[-1][2] < 2:
            runs[-1][2] = t["end"]
        else:
            runs.append([t["s"], t["t"], t["end"]])
    for s, a, b in runs:
        per[s]["longest"] = max(per[s]["longest"], b - a)
    total = sum(p["talk"] for p in per.values()) or 1
    for s, p in per.items():
        p["talk_pct"] = round(p["talk"] / total * 100, 1)
        p["wpm"] = round(p["words"] / (p["talk"] / 60), 0) if p["talk"] > 5 else 0
        p["monologues"] = sum(1 for r in runs if r[0] == s and r[2] - r[1] >= 90)
        p["talk"] = round(p["talk"], 1); p["longest"] = round(p["longest"], 1)
    return dict(per_speaker=per, duration=dur, speaker_count=len(per))


def trim(work, has_video, a, b):
    """Removes [a, b] seconds from the playback media. The original upload is kept."""
    keep = f"not(between(t,{a:.3f},{b:.3f}))"
    ffmpeg("-i", f"{work}/audio.mp3", "-af", f"aselect='{keep}',asetpts=N/SR/TB", "-c:a", "libmp3lame", "-b:a", "48k",
           f"{work}/audio.trim.mp3")
    os.replace(f"{work}/audio.trim.mp3", f"{work}/audio.mp3")
    if has_video:
        ffmpeg("-i", f"{work}/video.mp4", "-vf", f"select='{keep}',setpts=N/FRAME_RATE/TB",
               "-af", f"aselect='{keep}',asetpts=N/SR/TB", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", f"{work}/video.trim.mp4")
        os.replace(f"{work}/video.trim.mp4", f"{work}/video.mp4")
    return probe(f"{work}/audio.mp3")[0]
