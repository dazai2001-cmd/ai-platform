"""Check downloaded safetensors against their Hugging Face LFS SHA256 metadata."""
import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub._local_folder import get_local_download_paths

root = Path(__file__).resolve().parents[2] / "data/models/video-benchmark"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("folder", choices=["sd15", "animatediff", "cogvideox", "ltx", "realistic"])
args = parser.parse_args()
folder = root / args.folder
results = []
for path in folder.rglob("*.safetensors"):
    relative = str(path.relative_to(folder)).replace("\\", "/")
    metadata = get_local_download_paths(folder, relative).metadata_path.read_text(encoding="utf-8").splitlines()
    expected = metadata[1]
    if len(expected) != 64:
        raise RuntimeError("Expected SHA256 LFS metadata")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    item = {"file": relative, "bytes": path.stat().st_size, "sha256_matches": actual == expected}
    results.append(item)
    print(json.dumps(item), flush=True)
    if actual != expected:
        raise RuntimeError("Downloaded checkpoint checksum differs")
out = root.parents[1] / "cache/video_benchmark" / f"{args.folder}-checksums.json"
out.write_text(json.dumps(results, indent=2), encoding="utf-8")
