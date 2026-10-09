"""Stream FP32 safetensors into BF16 with bounded temporary memory.

Only task-owned checkpoints below data/models/video-benchmark may be converted.
The source is removed only with --remove-source after validating the new header.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2] / "data/models/video-benchmark"
SIZES = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4,
         "I16": 2, "I8": 1, "U8": 1, "BOOL": 1}


def convert(source: Path, target: Path, remove_source=False, owned_root=ROOT):
    import torch
    from safetensors import safe_open
    source = source.resolve()
    target = target.resolve()
    source.relative_to(owned_root.resolve())
    target.relative_to(owned_root.resolve())
    if source == target:
        raise ValueError("Conversion requires a separate destination")
    target.parent.mkdir(parents=True, exist_ok=True)
    with safe_open(source, framework="pt", device="cpu") as handle:
        metadata = dict(handle.metadata() or {})
        metadata.update(format="pt", benchmark_conversion="FP32 to BF16, streamed")
        header = {"__metadata__": metadata}
        offset = 0
        keys = list(handle.keys())
        for key in keys:
            view = handle.get_slice(key)
            shape, dtype = view.get_shape(), view.get_dtype()
            result_dtype = "BF16" if dtype == "F32" else dtype
            elements = 1
            for dimension in shape:
                elements *= dimension
            size = elements * SIZES[result_dtype]
            header[key] = {"dtype": result_dtype, "shape": shape, "data_offsets": [offset, offset + size]}
            offset += size
        encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
        encoded += b" " * ((-len(encoded)) % 8)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(struct.pack("<Q", len(encoded)))
            stream.write(encoded)
            for key in keys:
                view = handle.get_slice(key)
                shape = view.get_shape()
                row_elements = 1
                for dimension in shape[1:]:
                    row_elements *= dimension
                rows_per_chunk = max(1, (64 * 2**20) // (row_elements * SIZES[view.get_dtype()]))
                ranges = range(0, shape[0], rows_per_chunk) if shape else [0]
                for start in ranges:
                    tensor = view[start:min(start + rows_per_chunk, shape[0])] if shape else handle.get_tensor(key)
                    if tensor.dtype == torch.float32:
                        tensor = tensor.to(torch.bfloat16)
                    stream.write(tensor.contiguous().reshape(-1).view(torch.uint8).numpy().tobytes())
                    del tensor
    expected_size = 8 + len(encoded) + offset
    if temporary.stat().st_size != expected_size:
        raise RuntimeError("Converted checkpoint size is inconsistent")
    with safe_open(temporary, framework="pt", device="cpu") as check:
        if set(check.keys()) != set(keys):
            raise RuntimeError("Converted checkpoint keys differ")
    temporary.replace(target)
    if remove_source:
        source.unlink()
    return {"source": str(source), "target": str(target), "bytes": expected_size, "source_removed": remove_source}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--remove-source", action="store_true")
    args = parser.parse_args()
    print(json.dumps(convert(args.source, args.target, args.remove_source)), flush=True)


if __name__ == "__main__":
    main()
