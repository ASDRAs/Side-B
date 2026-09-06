"""Build a versioned, local-only model context for the inference image."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--clap", type=Path, required=True)
parser.add_argument("--svm", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if args.output.exists():
    raise SystemExit("Choose a new output directory; existing artifacts are preserved")
files = {
    f"clap/{name}": args.clap / name
    for name in (
        "config.json",
        "processor_config.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "model.safetensors",
    )
}
files.update(
    {
        f"svm/{name}": args.svm / name
        for name in ("svm_classifier.pkl", "label_encoder.pkl")
    }
)
checksums = {}
for name, source in files.items():
    target = args.output / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    with target.open("rb") as stream:
        checksums[name] = hashlib.file_digest(stream, "sha256").hexdigest()
version = (
    "clap-svm-"
    + hashlib.sha256(json.dumps(checksums, sort_keys=True).encode()).hexdigest()[:16]
)
(args.output / "manifest.json").write_text(
    json.dumps({"model_version": version, "sha256": checksums}, indent=2)
)
print(version)
