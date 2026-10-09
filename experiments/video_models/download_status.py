"""Report progress of this task's installers and checkpoint files only."""
import json
from pathlib import Path
import psutil

root = Path(__file__).resolve().parents[2]
state = {"disk_free_gib": round(psutil.disk_usage(str(root)).free / 2**30, 2),
         "ram_free_gib": round(psutil.virtual_memory().available / 2**30, 2), "installers": [], "files": []}
for process in psutil.process_iter(["pid", "cmdline"]):
    command = process.info["cmdline"] or []
    if "pip" not in command or not any(".venv-video-test" in part for part in command):
        continue
    item = {"pid": process.pid, "downloads": []}
    try:
        for opened in process.open_files():
            path = Path(opened.path)
            if path.exists() and path.stat().st_size > 100_000_000:
                item["downloads"].append({"file": path.name, "bytes": path.stat().st_size})
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    state["installers"].append(item)
model_roots = [root / "data/models/video-benchmark"]
locations = root / "data/cache/video_benchmark/model-locations.json"
if locations.is_file():
    external = Path(json.loads(locations.read_text(encoding="utf-8-sig"))["wan_models_root"])
    model_roots.append(external)
    state["external_cache_free_gib"] = round(psutil.disk_usage(str(external)).free / 2**30, 2)
for model_root in model_roots:
    for path in model_root.rglob("*"):
        if path.is_file() and path.suffix in {".incomplete", ".safetensors", ".pth"} and path.stat().st_size > 100_000_000:
            state["files"].append({"file": str(path), "gib": round(path.stat().st_size / 2**30, 2)})
print(json.dumps(state, indent=2), flush=True)
