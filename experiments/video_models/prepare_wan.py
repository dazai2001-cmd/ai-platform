"""Prepare BF16 Wan diffusion weights while preserving the FP32 VAE."""
import json
import hashlib
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from convert_checkpoint import convert
from huggingface_hub._local_folder import get_local_download_paths

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/models/video-benchmark/wan-raw"
DEST = ROOT / "data/models/video-benchmark/wan"
REPORTS = ROOT / "data/cache/video_benchmark"
REVISION = "0fad780a534b6463e45facd96134c9f345acfa5b"
REPOSITORY = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"


def main():
    global RAW, DEST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-root", type=Path, default=ROOT / "data/models/video-benchmark")
    parser.add_argument("--components", nargs="+", choices=["text_encoder", "transformer", "vae"],
                        default=["text_encoder", "transformer", "vae"])
    args = parser.parse_args()
    RAW = args.models_root.resolve() / "wan-raw"
    DEST = args.models_root.resolve() / "wan"
    os.environ.update(HF_HOME=str(REPORTS / "huggingface"), HF_HUB_DISABLE_XET="1",
                      HF_HUB_DISABLE_TELEMETRY="1")
    executable = shutil.which("hf")
    if not executable:
        raise RuntimeError("Install the Hugging Face CLI")
    subprocess.run([executable, "download", REPOSITORY, "--revision", REVISION,
                    "--local-dir", str(DEST), "--include", "*.json", "tokenizer/*", "README.md",
                    "--max-workers", "1"], check=True)
    inventory = json.loads((REPORTS / "model_inventory.json").read_text(encoding="utf-8"))
    record = next(item for item in inventory if item["repository"] == REPOSITORY)
    files = [item for item in record["files"] if item["name"].endswith(".safetensors")
             and item["name"].split("/", 1)[0] in args.components]
    conversions = []
    for item in files:
        name = item["name"]
        if name.startswith("text_encoder/") and (DEST / "text_encoder/model.safetensors").is_file():
            continue
        target = DEST / name
        if target.is_file():
            continue
        if shutil.disk_usage(str(args.models_root)).free < item["bytes"] * 1.6 + 2 * 2**30:
            raise RuntimeError("Not enough disk space for the next checkpoint conversion")
        print(json.dumps({"phase": "download", "file": name, "source_bytes": item["bytes"]}), flush=True)
        subprocess.run([executable, "download", REPOSITORY, name, "--revision", REVISION,
                        "--local-dir", str(RAW), "--max-workers", "1"], check=True)
        metadata = get_local_download_paths(RAW, name).metadata_path.read_text().splitlines()
        with (RAW / name).open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        if len(metadata[1]) != 64 or checksum != metadata[1]:
            raise RuntimeError(f"Checkpoint checksum mismatch: {name}")
        if name.startswith("vae/"):
            # Wan's official inference recipe keeps the decoder in FP32.
            source = (RAW / name).resolve()
            target = target.resolve()
            source.relative_to(args.models_root.resolve())
            target.relative_to(args.models_root.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            prepared = {"source": str(source), "target": str(target),
                        "bytes": target.stat().st_size, "source_removed": True,
                        "precision": "original FP32"}
        else:
            prepared = convert(RAW / name, target, remove_source=True, owned_root=args.models_root)
            prepared["precision"] = "BF16"
        conversions.append({**prepared, "source_sha256": checksum, "checksum_verified": True})
        print(json.dumps({"phase": "converted", **conversions[-1]}), flush=True)
        (REPORTS / "wan-conversion.json").write_text(json.dumps({
            "repository": REPOSITORY, "revision": REVISION, "conversions": conversions,
        }, indent=2), encoding="utf-8")
    for folder in dict.fromkeys(args.components):
        config_path = DEST / folder / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if "torch_dtype" in config:
            config["torch_dtype"] = "float32" if folder == "vae" else "bfloat16"
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        for index_path in (DEST / folder).glob("*.index.json"):
            if folder == "text_encoder" and (DEST / folder / "model.safetensors").is_file():
                continue
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index.setdefault("metadata", {})["total_size"] = sum(
                (DEST / folder / name).stat().st_size for name in set(index["weight_map"].values()))
            index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(json.dumps({"phase": "components_ready", "folder": str(DEST),
                      "components": list(dict.fromkeys(args.components))}), flush=True)


if __name__ == "__main__":
    main()
