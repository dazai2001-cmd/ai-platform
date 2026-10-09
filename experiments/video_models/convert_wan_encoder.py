"""Losslessly rename Wan's official BF16 UMT5 encoder into HF safetensors.

Native module names are defined in Wan2.1/wan/modules/t5.py. The entire key and
shape set must match the official Diffusers UMT5 configuration before writing.
Only task-owned benchmark paths may be read, replaced, or removed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
OWNED = ROOT / "data/models/video-benchmark"


def rename(key):
    if key == "token_embedding.weight":
        return "shared.weight"
    if key == "norm.weight":
        return "encoder.final_layer_norm.weight"
    match = re.fullmatch(r"blocks\.(\d+)\.(.+)", key)
    if not match:
        raise ValueError(f"Unknown native encoder key: {key}")
    block, suffix = match.groups()
    mapping = {
        "norm1.weight": "layer.0.layer_norm.weight",
        "norm2.weight": "layer.1.layer_norm.weight",
        "attn.q.weight": "layer.0.SelfAttention.q.weight",
        "attn.k.weight": "layer.0.SelfAttention.k.weight",
        "attn.v.weight": "layer.0.SelfAttention.v.weight",
        "attn.o.weight": "layer.0.SelfAttention.o.weight",
        "pos_embedding.embedding.weight": "layer.0.SelfAttention.relative_attention_bias.weight",
        "ffn.gate.0.weight": "layer.1.DenseReluDense.wi_0.weight",
        "ffn.fc1.weight": "layer.1.DenseReluDense.wi_1.weight",
        "ffn.fc2.weight": "layer.1.DenseReluDense.wo.weight",
    }
    return f"encoder.block.{block}.{mapping[suffix]}"


def convert(source, destination, remove_source=False):
    import torch
    from accelerate import init_empty_weights
    from safetensors import safe_open
    from transformers import UMT5Config, UMT5EncoderModel
    from huggingface_hub._local_folder import get_local_download_paths

    source, destination = source.resolve(), destination.resolve()
    source.relative_to(OWNED.resolve())
    destination.relative_to(OWNED.resolve())
    metadata = get_local_download_paths(source.parent, source.name).metadata_path.read_text().splitlines()
    with source.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    if len(metadata[1]) != 64 or checksum != metadata[1]:
        raise RuntimeError("Native checkpoint checksum does not match official HF metadata")

    config = UMT5Config.from_pretrained(destination, local_files_only=True)
    with init_empty_weights():
        expected = UMT5EncoderModel(config).state_dict()
    expected.pop("encoder.embed_tokens.weight")  # Shared embedding alias.
    weights = torch.load(source, map_location="cpu", weights_only=True, mmap=True)
    renamed = {rename(key): tensor for key, tensor in weights.items()}
    if len(renamed) != len(weights) or set(renamed) != set(expected):
        raise RuntimeError("Converted key set differs from the complete UMT5 encoder")
    for key, tensor in renamed.items():
        if tensor.shape != expected[key].shape or tensor.dtype != torch.bfloat16:
            raise RuntimeError(f"Unexpected native shape or precision: {key}")
    del expected

    header = {"__metadata__": {"format": "pt", "source": "Official Wan BF16 encoder; lossless key rename"}}
    offset = 0
    for key, tensor in renamed.items():
        size = tensor.numel() * tensor.element_size()
        header[key] = {"dtype": "BF16", "shape": list(tensor.shape), "data_offsets": [offset, offset + size]}
        offset += size
    encoded = json.dumps(header, separators=(",", ":")).encode()
    encoded += b" " * ((-len(encoded)) % 8)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "model.safetensors"
    temporary = target.with_suffix(".safetensors.tmp")
    with temporary.open("wb") as handle:
        handle.write(struct.pack("<Q", len(encoded)))
        handle.write(encoded)
        for tensor in renamed.values():
            flat = tensor.reshape(-1)
            for start in range(0, flat.numel(), 32 * 2**20):
                part = flat[start:start + 32 * 2**20]
                handle.write(part.view(torch.uint8).numpy().tobytes())
    if temporary.stat().st_size != 8 + len(encoded) + offset:
        raise RuntimeError("Converted checkpoint has an inconsistent byte count")
    # Windows charges private file mappings against the paging-file budget.
    # Keep small cloned endpoint samples, then release the 11 GB source map
    # before mapping the converted checkpoint for validation.
    endpoints = {key: (tensor[0].clone(), tensor[-1].clone())
                 for key, tensor in renamed.items()}
    del renamed, weights, tensor, flat, part
    import gc
    gc.collect()
    with safe_open(temporary, framework="pt", device="cpu") as check:
        if set(check.keys()) != set(endpoints):
            raise RuntimeError("Written safetensors key set differs")
        for key, (first, last) in endpoints.items():
            # Sample both ends of every tensor to catch serialization or mapping errors.
            actual = check.get_slice(key)
            if not torch.equal(actual[0], first) or not torch.equal(actual[-1], last):
                raise RuntimeError(f"Written checkpoint values differ: {key}")
    # Windows cannot unlink a file while a live tensor still maps its storage.
    del actual
    gc.collect()
    temporary.replace(target)
    config.torch_dtype = torch.bfloat16
    config.save_pretrained(destination)
    # from_pretrained prioritizes the single safetensors file over the old index.
    if remove_source:
        source.unlink()
    report = {"source_sha256": checksum, "source": str(source), "target": str(target),
              "keys": len(header) - 1, "bytes": target.stat().st_size,
              "key_shapes_verified": True, "endpoint_values_verified": True,
              "source_removed": remove_source}
    (ROOT / "data/cache/video_benchmark/wan-native-encoder-conversion.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--remove-source", action="store_true")
    parser.add_argument("--models-root", type=Path, default=OWNED)
    args = parser.parse_args()
    OWNED = args.models_root.resolve()
    convert(args.source, OWNED / "wan/text_encoder", args.remove_source)
