"""Validate streamed checkpoint conversion before applying it to large weights."""
import json
from pathlib import Path

import torch
from safetensors.torch import load_file, save_file
from convert_checkpoint import convert

root = Path(__file__).resolve().parents[2] / "data/models/video-benchmark/conversion-check"
root.mkdir(parents=True, exist_ok=True)
original = root / "original.safetensors"
converted = root / "converted.safetensors"
tensors = {
    "weights": torch.arange(4096 * 4096, dtype=torch.float32).reshape(4096, 4096) / 7,
    "scalar": torch.tensor(3.0),
    "count": torch.tensor(42, dtype=torch.int64),
    "half": torch.tensor([1.0, 2.0], dtype=torch.float16),
    "empty": torch.empty((0, 2)),
}
save_file(tensors, original)
convert(original, converted)
loaded = load_file(converted)
for key, value in tensors.items():
    expected = value.to(torch.bfloat16) if value.dtype == torch.float32 else value
    torch.testing.assert_close(loaded[key], expected, rtol=0, atol=0)
original.unlink()
converted.unlink()
print(json.dumps({"checkpoint_conversion": "passed", "cases": list(tensors)}), flush=True)
