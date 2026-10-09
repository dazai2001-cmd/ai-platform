"""Record public model revisions and file sizes without printing credentials."""
from __future__ import annotations

import json
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/cache/video_benchmark"
REPOS = [
    "zai-org/CogVideoX-2b",
    "Lightricks/LTX-Video-0.9.6-distilled",
    "Lightricks/LTX-Video",
    "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
    "guoyww/animatediff-motion-adapter-v1-5-2",
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    api = HfApi()
    for repo in REPOS:
        try:
            info = api.model_info(repo, files_metadata=True)
            record = {
                "repository": repo,
                "revision": info.sha,
                "gated": info.gated,
                "files": [{"name": f.rfilename, "bytes": f.size} for f in info.siblings],
            }
        except Exception as exc:
            record = {"repository": repo, "error_type": type(exc).__name__}
        records.append(record)
        print(json.dumps(record), flush=True)
    (OUT / "model_inventory.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
