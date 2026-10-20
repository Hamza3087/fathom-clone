import glob, json, os, sys, time, logging, warnings
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import xml.etree.ElementTree as ET
from pyannote.core import Annotation, Segment
from pyannote.metrics.diarization import DiarizationErrorRate
from omegaconf import OmegaConf
D = "/root/sounding/data/icsi"; OUT = D + "/out"; os.makedirs(OUT, exist_ok=True)
MEETINGS = sys.argv[1:] or ["Bmr007", "Bmr019", "Bmr024"]

def reference(mid):
    ref = Annotation()
    for f in glob.glob(f"{D}/ICSI/Segments/{mid}.*.segs.xml"):
        for s in ET.parse(f).getroot():
            if s.get("starttime") and s.get("participant"):
                ref[Segment(float(s.get("starttime")), float(s.get("endtime")))] = s.get("participant")
    return ref

def score(name, mid, hyp, ref, el, dur):
    d0, d25 = DiarizationErrorRate(collar=0.0), DiarizationErrorRate(collar=0.25)
    uem = ref.get_timeline().extent()
    a, b = d0(ref, hyp, uem=uem), d25(ref, hyp, uem=uem)
    c = d0.compute_components(ref, hyp, uem=uem); tot = c["total"]
    r = dict(model=name, meeting=mid, der=round(a * 100, 1), der_c25=round(b * 100, 1), miss=round(c["missed detection"] / tot * 100, 1),
             fa=round(c["false alarm"] / tot * 100, 1), conf=round(c["confusion"] / tot * 100, 1),
             ref_spk=len(ref.labels()), hyp_spk=len(hyp.labels()), sec=round(el, 1), audio_min=round(dur / 60, 1))
    print(json.dumps(r), flush=True); return r

import soundfile as sf
res = []
# --- clustering diarizer (TitaNet), no speaker cap
from nemo.collections.asr.models import ClusteringDiarizer
cfg = OmegaConf.create(dict(
    num_workers=0, sample_rate=16000, batch_size=8, device="cuda", verbose=False,
    diarizer=dict(manifest_filepath=f"{OUT}/manifest.json", out_dir=OUT, oracle_vad=False, collar=0.25, ignore_overlap=True,
        vad=dict(model_path="vad_multilingual_marblenet", external_vad_manifest=None,
                 parameters=dict(window_length_in_sec=0.63, shift_length_in_sec=0.08, smoothing="median", overlap=0.5, onset=0.9, offset=0.5,
                                 pad_onset=0, pad_offset=0, min_duration_on=0, min_duration_off=0.6, filter_speech_first=True)),
        speaker_embeddings=dict(model_path="titanet_large",
                 parameters=dict(window_length_in_sec=[3.0, 2.5, 2.0, 1.5, 1.0, 0.5], shift_length_in_sec=[1.5, 1.25, 1.0, 0.75, 0.5, 0.25],
                                 multiscale_weights=[1, 1, 1, 1, 1, 1], save_embeddings=False)),
        clustering=dict(parameters=dict(oracle_num_speakers=False, max_num_speakers=12, enhanced_count_thres=80, max_rp_threshold=0.25,
                                        sparse_search_volume=30, maj_vote_spk_count=False, chunk_cluster_count=50, embeddings_per_chunk=10000)),
        msdd_model=dict(model_path=None), asr=dict(model_path=None))))
for mid in MEETINGS:
    wav = f"{D}/{mid}.wav"; dur = sf.info(wav).duration
    with open(f"{OUT}/manifest.json", "w") as f:
        f.write(json.dumps(dict(audio_filepath=wav, offset=0, duration=None, label="infer", text="-", num_speakers=None, rttm_filepath=None, uem_filepath=None)) + "\n")
    t0 = time.time(); ClusteringDiarizer(cfg=cfg).diarize(); el = time.time() - t0
    hyp = Annotation()
    for line in open(f"{OUT}/pred_rttms/{mid}.rttm"):
        p = line.split(); hyp[Segment(float(p[3]), float(p[3]) + float(p[4]))] = p[7]
    res.append(score("clustering-titanet", mid, hyp, reference(mid), el, dur))
# --- Sortformer v2 (4-speaker cap), for comparison
from nemo.collections.asr.models import SortformerEncLabelModel
dm = SortformerEncLabelModel.restore_from("/root/sounding/models/diar_streaming_sortformer_4spk-v2.nemo", map_location="cuda").eval()
sm = dm.sortformer_modules; sm.chunk_len, sm.chunk_right_context, sm.fifo_len, sm.spkcache_update_period, sm.spkcache_len = 340, 40, 40, 300, 188
for mid in MEETINGS:
    wav = f"{D}/{mid}.wav"; dur = sf.info(wav).duration
    t0 = time.time(); seg = dm.diarize(audio=wav, batch_size=1)[0]; el = time.time() - t0
    hyp = Annotation()
    for s in seg:
        a, b, spk = s.split(); hyp[Segment(float(a), float(b))] = spk
    res.append(score("sortformer-v2", mid, hyp, reference(mid), el, dur))
json.dump(res, open(f"{OUT}/results.json", "w"), indent=1)
print("DONE")
