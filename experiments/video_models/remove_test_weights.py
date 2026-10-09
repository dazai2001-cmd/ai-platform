"""Reclaim only newly downloaded benchmark weights after their test finishes."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OWNED = (ROOT / "data/models/video-benchmark").resolve()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("folder", choices=["animatediff", "sd15", "cogvideox", "ltx"])
parser.add_argument("--component", choices=["text_encoder", "transformer", "vae", "unet"])
args = parser.parse_args()
target = (OWNED / args.folder / (args.component or "")).resolve()
target.relative_to(OWNED)
removed = []
for path in target.rglob("*.safetensors"):
    resolved = path.resolve()
    resolved.relative_to(target)
    removed.append({"file": str(resolved.relative_to(OWNED)), "bytes": resolved.stat().st_size})
    resolved.unlink()
record = {"at": datetime.now(timezone.utc).isoformat(), "removed": removed,
          "gib_reclaimed": round(sum(item["bytes"] for item in removed) / 2**30, 2)}
with (ROOT / "data/cache/video_benchmark/weight-cleanup.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")
print(json.dumps(record), flush=True)
