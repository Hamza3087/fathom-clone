"""Child process for the GPU speech stage of one recording.
Usage: python -m sounding.speech <audio16k.wav> <duration_seconds> <work_dir>  -> writes <work_dir>/speech.json"""
import json, logging, sys
from . import media

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
wav, dur, work = sys.argv[1], float(sys.argv[2]), sys.argv[3]
res = media.speech(wav, dur, work)
json.dump(res, open(f"{work}/speech.json", "w"))
