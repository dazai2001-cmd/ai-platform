"""Find small official checkpoint variants and compatible LTX configuration."""
import json
from pathlib import Path
from huggingface_hub import HfApi

api = HfApi()
records = []
for repo in ["Wan-AI/Wan2.1-T2V-1.3B-Diffusers", "Wan-AI/Wan2.1-T2V-1.3B", "Lightricks/LTX-Video-0.9.5"]:
    try:
        refs = api.list_repo_refs(repo)
        info = api.model_info(repo, files_metadata=True)
        data = {"repository": repo, "revision": info.sha,
                "branches": [r.name for r in refs.branches],
                "files": [{"name": f.rfilename, "bytes": f.size} for f in info.siblings]}
    except Exception as exc:
        data = {"repository": repo, "error_type": type(exc).__name__}
    records.append(data)
    print(json.dumps(data), flush=True)
out = Path(__file__).resolve().parents[2] / "data/cache/video_benchmark/variant_inventory.json"
out.write_text(json.dumps(records, indent=2), encoding="utf-8")
