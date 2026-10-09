"""Compare file-backed block loading with complete T5 and UMT5 forward passes."""
import json
from pathlib import Path

import torch
from transformers import T5Config, T5EncoderModel, UMT5Config, UMT5EncoderModel
from layer_encoder import load_encoder

root = Path(__file__).resolve().parents[2]
owned = (root / "data/models/video-benchmark").resolve()
checks = []
torch.set_num_threads(2)
for name, config_cls, model_cls, dtype in [
    ("t5", T5Config, T5EncoderModel, torch.float16),
    ("umt5", UMT5Config, UMT5EncoderModel, torch.bfloat16),
]:
    torch.manual_seed(42)
    config = config_cls(vocab_size=64, d_model=32, d_ff=80, d_kv=8,
                        num_heads=4, num_layers=2, dropout_rate=0,
                        feed_forward_proj="gated-gelu")
    original = model_cls(config).to(dtype=dtype).eval()
    if dtype == torch.float16:
        for key, module in original.named_modules():
            if key.split(".")[-1] in original._keep_in_fp32_modules:
                module.to(torch.float32)
    folder = owned / "encoder-self-check" / name
    original.save_pretrained(folder, safe_serialization=True)
    staged = load_encoder(folder, model_cls, dtype, torch.device("cpu"))
    ids = torch.tensor([[12, 7, 28, 2, 4, 1, 0, 0], [9, 5, 3, 11, 1, 0, 0, 0]])
    mask = (ids != 0).long()
    with torch.inference_mode():
        expected = original(input_ids=ids, attention_mask=mask).last_hidden_state
        actual = staged(input_ids=ids, attention_mask=mask).last_hidden_state
        second = staged(input_ids=ids, attention_mask=mask).last_hidden_state
    assert torch.equal(expected, actual), name
    assert torch.equal(actual, second), name
    assert all(next(block.parameters()).device.type == "meta" for block in staged.encoder.block)
    checks.append({"architecture": name, "dtype": str(dtype), "forward_exact_match": True,
                   "repeated_forward_exact_match": True, "blocks_released": True})
    del original, staged
    for path in folder.iterdir():
        path.resolve().relative_to(owned)
        if path.is_file():
            path.unlink()
(root / "data/cache/video_benchmark/encoder-self-check.json").write_text(json.dumps(checks, indent=2))
print(json.dumps(checks))
