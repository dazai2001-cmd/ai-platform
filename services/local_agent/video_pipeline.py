"""Offline LTX 2B distilled inference using the locally validated settings.

Imported only by the isolated CUDA worker, never by the web API.
"""
from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
import shutil


def encode_prompt(prompt: str, text_path: Path, emit):
    import torch
    from diffusers import LTXPipeline
    from transformers import AutoTokenizer, T5EncoderModel
    from services.local_agent.layer_encoder import load_encoder

    emit(progress=8, message="Encoding your video prompt")
    encoder = load_encoder(text_path / "text_encoder", T5EncoderModel, torch.float16, torch.device("cuda"))
    tokenizer = AutoTokenizer.from_pretrained(text_path / "tokenizer", local_files_only=True)
    # Prompt encoding runs before loading diffusion/decoder weights. Only one
    # original encoder block is resident on the GPU at any time.
    pipe = object.__new__(LTXPipeline)
    pipe.register_modules(text_encoder=encoder, tokenizer=tokenizer, transformer=None, vae=None, scheduler=None)
    with torch.inference_mode():
        positive, mask, _, _ = pipe.encode_prompt(
            prompt=prompt, do_classifier_free_guidance=False, device=torch.device("cuda"),
            num_videos_per_prompt=1, max_sequence_length=128,
        )
    tensors = {"prompt_embeds": positive.cpu().to(torch.bfloat16), "prompt_attention_mask": mask.cpu()}
    if not all(torch.isfinite(value).all() for value in tensors.values()):
        raise FloatingPointError("Video prompt encoding contains nonfinite values.")
    del pipe, encoder, tokenizer, positive, mask
    gc.collect()
    torch.cuda.empty_cache()
    return tensors


def generate(spec: dict, checkpoint_path: Path, config_path: Path, text_path: Path, offload_path: Path, emit):
    import torch
    from diffusers import FlowMatchEulerDiscreteScheduler, LTXPipeline
    from diffusers.utils.torch_utils import randn_tensor
    from safetensors import safe_open
    from accelerate import cpu_offload, disk_offload

    width, height, seconds, fps = (spec[name] for name in ("width", "height", "seconds", "fps"))
    if (spec["model_id"] != "ltx-video-2b-distilled" or
        any(type(value) is not int for value in (width, height, seconds, fps)) or
        min(width, height) < 256 or max(width, height) > 704 or width % 64 or height % 64 or
        width * height > 704 * 512 or not 1 <= seconds <= 6 or fps != 25):
        raise ValueError("LTX previews require bounded dimensions, 1-6 seconds and 25 fps.")
    with safe_open(checkpoint_path, framework="pt", device="cpu") as handle:
        metadata = json.loads(handle.metadata()["config"])
    sigmas = metadata["allowed_inference_steps"]
    if sigmas != [1.0, 0.9937, 0.9875, 0.9812, 0.975, 0.9094, 0.725, 0.4219]:
        raise ValueError("Install the tested LTX 2B 0.9.6 distilled checkpoint.")

    prompt = f"{spec['prompt'].strip()}. Visual style: {spec['style'].strip()}."
    encoded = encode_prompt(prompt, text_path, emit)
    emit(progress=18, message="Loading LTX video model")

    class CheckpointScheduler(FlowMatchEulerDiscreteScheduler):
        def set_timesteps(self, num_inference_steps=None, device=None, sigmas=None, mu=None, timesteps=None):
            # Diffusers 0.35 supplies uniform sigmas. This distilled checkpoint
            # requires its published noise levels and stochastic sampling.
            return super().set_timesteps(device=device, sigmas=metadata["allowed_inference_steps"], mu=mu)

    pipe = LTXPipeline.from_single_file(
        str(checkpoint_path), config=str(config_path), text_encoder=None, tokenizer=None,
        torch_dtype=torch.bfloat16, local_files_only=True,
    )
    pipe.scheduler = CheckpointScheduler.from_config(pipe.scheduler.config, stochastic_sampling=True)
    pipe.vae.enable_tiling()
    pipe.vae.use_framewise_decoding = True
    # Keep transformer weights on disk instead of retaining a multi-GB CPU copy
    # beside Docker's API/frontend. The cache is scoped to the exact checkpoint,
    # configuration and supported Diffusers converter version.
    stat = checkpoint_path.stat()
    fingerprint = hashlib.sha256()
    fingerprint.update(f"{checkpoint_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:diffusers-0.35.2".encode())
    for name in ("model_index.json", "transformer/config.json"):
        fingerprint.update((config_path / name).read_bytes())
    offload_path.mkdir(parents=True, exist_ok=True)
    cache = offload_path / fingerprint.hexdigest()[:20]
    cache.mkdir(exist_ok=True)
    index_path = cache / "index.json"
    state = pipe.transformer.state_dict()
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        valid = set(index) == set(state) and all(
            (cache / f"{key}.dat").is_file() and
            (cache / f"{key}.dat").stat().st_size == value.numel() * value.element_size()
            for key, value in state.items()
        )
    except (OSError, ValueError, TypeError):
        valid = False
    if not valid:
        index_path.unlink(missing_ok=True)
        required = sum(value.numel() * value.element_size() for value in state.values())
        free = shutil.disk_usage(cache).free
        if free < required + 512 * 1024**2:
            raise ValueError(f"LTX needs {(required + 512 * 1024**2) / 1024**3:.1f} GiB free disk space for its local offload cache; {free / 1024**3:.1f} GiB available.")
        emit(progress=23, message="Preparing video model")
        from accelerate.utils import set_module_tensor_to_device
        from accelerate.utils.offload import offload_weight, save_offload_index
        index = {}
        for key in tuple(state):
            value = state.pop(key)
            offload_weight(value, key, str(cache), index=index)
            # Release each original tensor immediately instead of holding the
            # whole state dict throughout the first cache write.
            set_module_tensor_to_device(pipe.transformer, key, "meta")
            del value
        save_offload_index(index, str(cache))
    del state
    emit(progress=23, message="Preparing video model")
    disk_offload(pipe.transformer, str(cache), execution_device=torch.device("cuda"), offload_buffers=True)
    # LTX's normalization buffers are read outside VAE.forward by the pipeline.
    # Keep those small buffers resident while offloading the decoder parameters.
    cpu_offload(pipe.vae, execution_device=torch.device("cuda"), offload_buffers=False)
    gc.collect()
    torch.cuda.empty_cache()
    steps = len(sigmas)

    def progress_callback(_pipeline, step, _timestep, kwargs):
        if not torch.isfinite(kwargs["latents"]).all():
            raise FloatingPointError("Video generation contains nonfinite values.")
        emit(progress=25 + round(60 * (step + 1) / steps), message=f"Generating video step {step + 1}/{steps}")
        return kwargs

    frames = ((seconds * fps - 1 + 7) // 8) * 8 + 1
    generator = torch.Generator(device="cpu").manual_seed(spec["seed"])
    with torch.inference_mode():
        latents = pipe(
            **encoded, width=width, height=height, num_frames=frames, frame_rate=fps,
            num_inference_steps=steps, guidance_scale=1.0, decode_timestep=0.05, decode_noise_scale=0.025,
            generator=generator, callback_on_step_end=progress_callback, output_type="latent",
        ).frames
        # The denoiser is no longer needed. Drop its CPU offload weights before
        # decoding so the transformer and VAE peaks cannot overlap in system RAM.
        pipe.transformer = None
        del encoded
        gc.collect()
        torch.cuda.empty_cache()
        emit(progress=88, message="Decoding video frames")
        latents = pipe._unpack_latents(
            latents, (frames - 1) // pipe.vae_temporal_compression_ratio + 1,
            height // pipe.vae_spatial_compression_ratio, width // pipe.vae_spatial_compression_ratio,
            pipe.transformer_spatial_patch_size, pipe.transformer_temporal_patch_size,
        )
        latents = pipe._denormalize_latents(
            latents, pipe.vae.latents_mean, pipe.vae.latents_std, pipe.vae.config.scaling_factor,
        ).to(torch.bfloat16)
        timestep = None
        if pipe.vae.config.timestep_conditioning:
            noise = randn_tensor(latents.shape, generator=generator, device=latents.device, dtype=latents.dtype)
            latents = 0.975 * latents + 0.025 * noise
            timestep = torch.tensor([0.05], device=latents.device, dtype=latents.dtype)
        decoded = pipe.vae.decode(latents.to(pipe.vae.dtype), timestep, return_dict=False)[0]
        if not torch.isfinite(decoded).all():
            raise FloatingPointError("Video decoding contains nonfinite values.")
        return pipe.video_processor.postprocess_video(decoded, output_type="pil")[0]
