"""Resume bounded HTTP reads when the Hub CLI stalls on large encoder shards.

Only the pinned encoder used by the local LTX worker is downloaded. Each shard
is SHA-256 checked against the official Hub metadata before it is installed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time

import requests
from huggingface_hub import get_hf_file_metadata, hf_hub_url

REPO = "zai-org/CogVideoX-2b"
REVISION = "1137dacfc2c9c012bed6a0793f4ecf2ca8e7ba01"
SHARDS = ("model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors")


def download(root: Path, filename: str):
    url = hf_hub_url(REPO, f"text_encoder/{filename}", revision=REVISION)
    metadata = get_hf_file_metadata(url, timeout=30)
    expected_hash, total = metadata.etag, metadata.size
    if len(expected_hash) != 64 or not total:
        raise ValueError("Official checkpoint checksum or size is missing.")
    destination = root / "text_encoder" / filename
    partial = destination.with_suffix(".safetensors.partial")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        with destination.open("rb") as handle:
            if destination.stat().st_size == total and hashlib.file_digest(handle, "sha256").hexdigest() == expected_hash:
                print(json.dumps({"file": filename, "verified": True, "cached": True}), flush=True)
                return
        raise ValueError("An existing checkpoint has a different checksum; preserve it and choose a new directory.")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > total:
        raise ValueError("Partial checkpoint is larger than the official file.")
    last_report = 0
    with partial.open("ab") as output:
        while offset < total:
            end = min(offset + 64 * 1024**2, total) - 1
            for attempt in range(5):
                try:
                    # A Range request bounds proxy buffering and the size of a
                    # retry. Verify Content-Range so cached responses cannot be
                    # mistaken for the requested checkpoint bytes.
                    with requests.get(url, headers={"Range": f"bytes={offset}-{end}"}, stream=True, timeout=(15, 45)) as response:
                        response.raise_for_status()
                        if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {offset}-{end}/{total}":
                            raise ValueError("The server did not return the requested checkpoint range.")
                        written = 0
                        for chunk in response.iter_content(1024**2):
                            written += len(chunk)
                            if written > end - offset + 1:
                                raise ValueError("Checkpoint range exceeded its declared length.")
                            output.write(chunk)
                        if written != end - offset + 1:
                            raise OSError("Incomplete checkpoint range.")
                    output.flush()
                    offset = end + 1
                    if offset - last_report >= 256 * 1024**2 or offset == total:
                        print(json.dumps({"file": filename, "percent": round(100 * offset / total, 1)}), flush=True)
                        last_report = offset
                    break
                except (requests.RequestException, OSError):
                    output.seek(offset)
                    output.truncate()
                    if attempt == 4:
                        raise
                    time.sleep(min(2**attempt, 8))
    with partial.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_hash:
        raise ValueError("Downloaded checkpoint checksum differs from the official Hub checksum.")
    partial.replace(destination)
    print(json.dumps({"file": filename, "verified": True, "sha256": digest}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Local LTX text encoder/tokenizer root")
    args = parser.parse_args()
    root = Path(args.output).resolve()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda filename: download(root, filename), SHARDS))


if __name__ == "__main__":
    main()
