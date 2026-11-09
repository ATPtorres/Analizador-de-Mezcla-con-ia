import argparse
import json
import shutil
import tempfile
from pathlib import Path

from .dsp import AudioError, MAX_BYTES, analyze

parser = argparse.ArgumentParser(description="Analiza una mezcla con MixAI.")
parser.add_argument("audio", type=Path)
args = parser.parse_args()
try:
    if args.audio.stat().st_size > MAX_BYTES:
        raise AudioError("El archivo excede 80 MiB.")
    with tempfile.TemporaryDirectory(prefix="mixai-") as directory:
        source = Path(directory) / "input"
        shutil.copyfile(args.audio, source)
        print(json.dumps(analyze(source), ensure_ascii=False, indent=2, allow_nan=False))
except (AudioError, OSError) as exc:
    parser.exit(2, f"mixai: {exc}\n")
