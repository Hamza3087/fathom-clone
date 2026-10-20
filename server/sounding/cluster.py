"""Speaker clustering with TitaNet embeddings over given speech regions (see media.cluster_speakers)."""
import gc, json, logging, os


def cluster(wav, regions, out, titanet):
    from omegaconf import OmegaConf
    from nemo.collections.asr.models import ClusteringDiarizer
    import torch
    uid = os.path.splitext(os.path.basename(wav))[0]
    with open(f"{out}/vad.json", "w") as f:
        for a, b in regions:
            f.write(json.dumps(dict(audio_filepath=wav, offset=round(a, 3), duration=round(b - a, 3), label="UNK", uniq_id=uid)) + "\n")
    with open(f"{out}/manifest.json", "w") as f:
        f.write(json.dumps(dict(audio_filepath=wav, offset=0, duration=None, label="infer", text="-", num_speakers=None,
                                rttm_filepath=None, uem_filepath=None)) + "\n")
    cfg = OmegaConf.create(dict(num_workers=0, sample_rate=16000, batch_size=8, device="cuda", verbose=False, diarizer=dict(
        manifest_filepath=f"{out}/manifest.json", out_dir=out, oracle_vad=False, collar=0.25, ignore_overlap=True,
        vad=dict(model_path=None, external_vad_manifest=f"{out}/vad.json", parameters=dict(
            window_length_in_sec=0.63, shift_length_in_sec=0.08, smoothing=False, overlap=0.5, onset=0.5, offset=0.5,
            pad_onset=0, pad_offset=0, min_duration_on=0, min_duration_off=0, filter_speech_first=True)),
        speaker_embeddings=dict(model_path=titanet, parameters=dict(
            window_length_in_sec=[3.0, 2.5, 2.0, 1.5, 1.0, 0.5], shift_length_in_sec=[1.5, 1.25, 1.0, 0.75, 0.5, 0.25],
            multiscale_weights=[1, 1, 1, 1, 1, 1], save_embeddings=False)),
        clustering=dict(parameters=dict(oracle_num_speakers=False, max_num_speakers=12, enhanced_count_thres=80, max_rp_threshold=0.25,
                                        sparse_search_volume=30, maj_vote_spk_count=False, chunk_cluster_count=50, embeddings_per_chunk=10000)),
        msdd_model=dict(model_path=None), asr=dict(model_path=None))))
    logging.disable(logging.WARNING)
    diarizer = ClusteringDiarizer(cfg=cfg)
    try:
        diarizer.diarize()
    finally:
        logging.disable(logging.NOTSET)
        del diarizer; gc.collect(); torch.cuda.empty_cache()
    res = []
    for line in open(f"{out}/pred_rttms/{uid}.rttm"):
        p = line.split(); res.append([float(p[3]), float(p[3]) + float(p[4]), p[7]])
    return res
