import os, io, re, sys, json, time, glob, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import numpy as np, soundfile as sf, pyarrow.parquet as pq, torch
import xml.etree.ElementTree as ET
from pyannote.core import Annotation, Segment
from pyannote.metrics.diarization import DiarizationErrorRate
S = "/root/sounding"; M = S + "/models"
log = open(S + "/results/pipeline.txt", "w")
def say(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); log.write(s + "\n"); log.flush()

# ---------- data ----------
rows = pq.read_table(S + "/data/ami-sdm-test-0.parquet").to_pylist()
meets = []
for r in rows:
    mid = r["audio"]["path"].split(".")[0]; p = f"{S}/wav/{mid}.wav"
    if not os.path.exists(p):
        x, sr = sf.read(io.BytesIO(r["audio"]["bytes"])); sf.write(p, x if x.ndim == 1 else x.mean(1), sr)
    ref = Annotation()
    for a, b, spk in zip(r["timestamps_start"], r["timestamps_end"], r["speakers"]): ref[Segment(a, b)] = spk
    meets.append(dict(id=mid, wav=p, dur=sf.info(p).duration, ref=ref))
del rows

FILL = {"uh", "um", "mm", "hmm", "mm-hmm", "uh-huh", "mhm", "ah", "oh", "hm"}
def norm(t):
    t = t.lower().replace("-", " ")
    return [w for w in re.sub(r"[^a-z0-9' ]", " ", t).split() if w not in FILL]
def ref_words(mid):
    ws = []
    for f in glob.glob(f"{S}/data/ami_ann/words/{mid}.*.words.xml"):
        for w in ET.parse(f).getroot():
            if w.tag == "w" and w.get("punc") != "true" and w.text and w.get("starttime"):
                ws.append((float(w.get("starttime")), w.text))
    return norm(" ".join(t for _, t in sorted(ws)))
def wer(ref, hyp):
    d = np.arange(len(hyp) + 1)
    for i in range(1, len(ref) + 1):
        prev, d[0] = d.copy(), i
        for j in range(1, len(hyp) + 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (ref[i - 1] != hyp[j - 1]))
    return d[-1] / max(len(ref), 1)

res = {"meetings": {m["id"]: {"minutes": round(m["dur"] / 60, 1)} for m in meetings} if False else {}}
res = {"meetings": {m["id"]: {"minutes": round(m["dur"] / 60, 1), "ref_speakers": len(m["ref"].labels())} for m in meets}}

# ---------- diarization ----------
from nemo.collections.asr.models import SortformerEncLabelModel, ASRModel
dm = SortformerEncLabelModel.restore_from(M + "/diar_streaming_sortformer_4spk-v2.nemo", map_location="cuda").eval()
sm = dm.sortformer_modules  # model card "high latency" streaming config
sm.chunk_len, sm.chunk_right_context, sm.fifo_len, sm.spkcache_update_period, sm.spkcache_len = 340, 40, 40, 300, 188
der0, der25 = DiarizationErrorRate(collar=0.0), DiarizationErrorRate(collar=0.25)
t_all = 0
for m in meets:
    torch.cuda.synchronize(); t0 = time.time()
    seg = dm.diarize(audio=m["wav"], batch_size=1)[0]
    torch.cuda.synchronize(); el = time.time() - t0; t_all += el
    hyp = Annotation()
    for s in seg:
        a, b, spk = s.split(); hyp[Segment(float(a), float(b))] = spk
    m["hyp"] = hyp
    uem = m["ref"].get_timeline().extent()
    d0, d25 = der0(m["ref"], hyp, uem=uem), der25(m["ref"], hyp, uem=uem)
    comp = der0.compute_components(m["ref"], hyp, uem=uem)
    tot = comp["total"]
    res["meetings"][m["id"]].update(der=round(d0 * 100, 1), der_collar25=round(d25 * 100, 1),
        miss=round(comp["missed detection"] / tot * 100, 1), fa=round(comp["false alarm"] / tot * 100, 1),
        confusion=round(comp["confusion"] / tot * 100, 1), hyp_speakers=len(hyp.labels()), diar_sec=round(el, 1))
    say(f"DIAR {m['id']} {m['dur']/60:.0f}min  DER {d0*100:.1f}% (collar .25: {d25*100:.1f}%)  "
        f"miss {comp['missed detection']/tot*100:.1f} fa {comp['false alarm']/tot*100:.1f} conf {comp['confusion']/tot*100:.1f}  "
        f"spk {len(m['ref'].labels())}->{len(hyp.labels())}  {el:.1f}s")
res["diar_overall"] = dict(der=round(abs(der0) * 100, 1), der_collar25=round(abs(der25) * 100, 1),
                           rtf=round(t_all / sum(m["dur"] for m in meets), 4))
say("DIAR OVERALL", res["diar_overall"])
del dm; torch.cuda.empty_cache()

# ---------- ASR ----------
res["asr"] = {}
for name, path in [("parakeet-tdt-0.6b-v2", M + "/parakeet-tdt-0.6b-v2.nemo"), ("parakeet-rnnt-1.1b", M + "/parakeet-rnnt-1.1b.nemo")]:
    am = ASRModel.restore_from(path, map_location="cuda").eval()
    am.change_attention_model("rel_pos_local_attn", [256, 256])  # NeMo's documented long-audio mode
    am.change_subsampling_conv_chunking_factor(1)
    errs = refn = 0; t_all = 0; per = {}
    for m in meets:
        torch.cuda.synchronize(); t0 = time.time()
        with torch.autocast("cuda", dtype=torch.bfloat16):
            o = am.transcribe([m["wav"]], timestamps=True, batch_size=1, verbose=False)[0]
        torch.cuda.synchronize(); el = time.time() - t0; t_all += el
        r, h = ref_words(m["id"]), norm(o.text)
        w = wer(r, h); errs += w * len(r); refn += len(r); per[m["id"]] = round(w * 100, 1)
        say(f"ASR {name} {m['id']}  WER {w*100:.1f}%  ({len(r)} ref words, {len(h)} hyp)  {el:.1f}s")
        if name == "parakeet-tdt-0.6b-v2":
            m["words"] = [(x["word"], x["start"], x["end"]) for x in o.timestamp["word"]]
    res["asr"][name] = dict(wer=round(errs / refn * 100, 1), per_meeting=per, rtf=round(t_all / sum(m["dur"] for m in meets), 4),
                            gpu_gb=round(torch.cuda.max_memory_allocated() / 1e9, 1))
    say("ASR OVERALL", name, res["asr"][name])
    del am; torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()

# ---------- merge words + speakers into turns ----------
for m in meets:
    turns = []
    for w, a, b in m["words"]:
        best, ov = None, 0
        for seg, _, spk in m["hyp"].crop(Segment(a - 0.3, b + 0.3)).itertracks(yield_label=True):
            o = min(b, seg.end) - max(a, seg.start)
            if o > ov or best is None: best, ov = spk, o
        spk = best or (turns[-1]["speaker"] if turns else "speaker_0")
        if turns and turns[-1]["speaker"] == spk and a - turns[-1]["end"] < 1.5:
            turns[-1]["text"] += " " + w; turns[-1]["end"] = b
        else:
            turns.append(dict(speaker=spk, start=round(a, 2), end=b, text=w))
    json.dump(turns, open(f"{S}/results/{m['id']}.turns.json", "w"), indent=0)
json.dump(res, open(S + "/results/pipeline.json", "w"), indent=1)
say("DONE")
