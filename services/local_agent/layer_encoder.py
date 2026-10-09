"""Run a real T5/UMT5 encoder with one checkpoint block on the GPU at a time.

This avoids the full-precision CPU weight copy made by the ordinary loader.
Embedding lookups read only the requested rows on CPU. Transformer blocks use
the original weights; T5's protected output projections retain FP32 precision.
"""
import json
from collections import defaultdict
from pathlib import Path


def load_encoder(folder: Path, encoder_cls, dtype, device):
    import torch
    from torch import nn
    from torch.nn import functional as F
    from accelerate import init_empty_weights
    from safetensors import safe_open

    folder = folder.resolve()
    config = encoder_cls.config_class.from_pretrained(folder, local_files_only=True)
    with init_empty_weights():
        encoder = encoder_cls(config)
    encoder.to(dtype=dtype)
    encoder.eval()
    single = folder / "model.safetensors"
    if single.is_file():
        with safe_open(single, framework="pt", device="cpu") as handle:
            weight_map = {key: single.name for key in handle.keys()}
    else:
        weight_map = json.loads((folder / "model.safetensors.index.json").read_text())["weight_map"]
    shapes = {key: list(value.shape) for key, value in encoder.state_dict().items()}
    shapes.pop("encoder.embed_tokens.weight")  # Shared embedding alias.
    if set(shapes) != set(weight_map):
        raise RuntimeError("Checkpoint keys do not match the full encoder")
    grouped = defaultdict(list)
    for key, filename in weight_map.items():
        path = (folder / filename).resolve()
        path.relative_to(folder)
        grouped[path].append(key)
    for path, keys in grouped.items():
        with safe_open(path, framework="pt", device="cpu") as handle:
            for key in keys:
                if handle.get_slice(key).get_shape() != shapes[key]:
                    raise RuntimeError(f"Checkpoint shape differs: {key}")

    class MappedEmbedding(nn.Module):
        def __init__(self):
            super().__init__()
            # Pipeline dtype/device inspection needs a parameter, while lookup
            # uses the actual frozen checkpoint table instead of this placeholder.
            self.weight = nn.Parameter(torch.empty((0, config.d_model), device=device, dtype=dtype),
                                       requires_grad=False)

        def forward(self, ids):
            with safe_open(folder / weight_map["shared.weight"], framework="pt", device="cpu") as handle:
                table = handle.get_tensor("shared.weight")
                embedded = F.embedding(ids.to("cpu"), table)
            return embedded.to(device=device, dtype=dtype)

    embedding = MappedEmbedding()
    encoder.shared = embedding
    encoder.encoder.embed_tokens = embedding
    final_key = "encoder.final_layer_norm.weight"
    with safe_open(folder / weight_map[final_key], framework="pt", device="cpu") as handle:
        value = handle.get_tensor(final_key).to(device=device, dtype=dtype)
    encoder.encoder.final_layer_norm.weight = nn.Parameter(value, requires_grad=False)

    protected = encoder._keep_in_fp32_modules if dtype == torch.float16 else []
    def attach(block, index):
        prefix = f"encoder.block.{index}."
        sources = defaultdict(list)
        for key, filename in weight_map.items():
            if key.startswith(prefix):
                sources[filename].append(key)

        def before(module, _args):
            state = {}
            for filename, keys in sources.items():
                with safe_open(folder / filename, framework="pt", device="cpu") as handle:
                    for key in keys:
                        precision = torch.float32 if any(part in key.split(".") for part in protected) else dtype
                        state[key[len(prefix):]] = handle.get_tensor(key).to(device=device, dtype=precision)
            module.load_state_dict(state, strict=True, assign=True)
            module.requires_grad_(False)

        def after(module, _args, output):
            module.to(device="meta")
            return output

        block.register_forward_pre_hook(before)
        block.register_forward_hook(after)

    for index, block in enumerate(encoder.encoder.block):
        attach(block, index)
    encoder.hf_device_map = {"embedding_lookup": "cpu:file", "encoder_blocks": f"{device}:one_at_a_time"}
    return encoder
