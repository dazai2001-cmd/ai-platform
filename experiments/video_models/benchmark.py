"""Bounded, offline video experiments; this does not register production tools.

Run in .venv-video-test after downloading the pinned local checkpoints.
Each attempt is a separate process so GPU allocations are released between models.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "data/models/video-benchmark"
OUTPUT = ROOT / "data/cache/video_benchmark"
PROMPT = (
    "A small red toy car drives slowly from left to right across a wooden table. "
    "Static camera, soft daylight, realistic photography, smooth continuous movement."
)
NEGATIVE = "blurry, distorted, flickering, low quality"


def emit(**values):
    print(json.dumps(values), flush=True)


def gpu_sample():
    try:
        value = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"], text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).splitlines()[0]
        used, total, load, temperature = [int(v.strip()) for v in value.split(",")]
        return {"used_mib": used, "total_mib": total, "utilization": load, "temperature": temperature}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def run_monitored(args):
    import psutil
    OUTPUT.mkdir(parents=True, exist_ok=True)
    tag = args.tag or f"{args.action}-{args.model}"
    log_path = OUTPUT / f"{tag}.log"
    report_path = OUTPUT / f"{tag}.json"
    control_path = OUTPUT / f"{tag}.stop"
    control_path.unlink(missing_ok=True)
    started = time.monotonic()
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    report = {
        "action": args.action, "model": args.model, "tag": tag,
        "prompt": PROMPT, "negative_prompt": NEGATIVE, "seed": 42,
        "width": args.width, "height": args.height, "frames_requested": args.frames,
        "steps": args.steps, "fps": args.fps,
        "offload": args.offload,
        "encoder_offload": args.encoder_offload,
        "animatediff_beta_schedule": "linear" if args.model == "animatediff" and args.action == "generate" else None,
        "animatediff_base": args.sd_base if args.model == "animatediff" and args.action == "generate" else None,
        "motion_chunk_size": args.motion_chunk_size if args.model == "animatediff" and args.action == "generate" else None,
        "motion_standard_init": args.motion_standard_init if args.model == "animatediff" and args.action == "generate" else None,
        "framewise_vae": args.framewise_vae,
        "models_root": str(MODELS),
        "wan_vae_dtype": "float32" if args.model == "wan" and args.action == "generate" else None,
        "wan_vae_storage": "owned_cpu_copy" if args.model == "wan" and args.action == "generate" else None,
        "resume_latents": str(args.resume_latents) if args.resume_latents else None,
        "ram_total_mib": round(psutil.virtual_memory().total / 2**20),
        "ram_available_start_mib": round(psutil.virtual_memory().available / 2**20),
        "gpu_start": gpu_sample(), "peak_process_rss_mib": 0,
        "minimum_available_ram_mib": round(psutil.virtual_memory().available / 2**20),
        "gpu_peak_used_mib": 0, "status": "running", "log": str(log_path),
    }
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        child = psutil.Process(process.pid)
        last_gpu = 0.0
        low_memory_samples = 0
        while process.poll() is None:
            now = time.monotonic()
            try:
                descendants = child.children(recursive=True)
                report["peak_process_rss_mib"] = max(report["peak_process_rss_mib"],
                    round(sum(p.memory_info().rss for p in [child, *descendants] if p.is_running()) / 2**20))
            except psutil.NoSuchProcess:
                break
            available = round(psutil.virtual_memory().available / 2**20)
            report["minimum_available_ram_mib"] = min(report["minimum_available_ram_mib"], available)
            low_memory_samples = low_memory_samples + 1 if available < args.min_available_ram else 0
            if now - last_gpu >= 5:
                sample = gpu_sample()
                report["gpu_peak_used_mib"] = max(report["gpu_peak_used_mib"], sample.get("used_mib", 0))
                report["gpu_last"] = sample
                report["elapsed_seconds"] = round(now - started, 2)
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                last_gpu = now
            reason = None
            if now - started > args.timeout:
                reason = "timeout"
            elif low_memory_samples >= 10:
                reason = "system_ram_budget"
            elif control_path.is_file():
                reason = control_path.read_text(encoding="utf-8").strip()[:200] or "manual_stop"
            if reason:
                # Windows venv executables redirect to a child interpreter.
                # Stop and measure the full tree, not just the small launcher.
                owned = [*reversed(child.children(recursive=True)), child]
                for owned_process in owned:
                    try:
                        owned_process.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(owned, timeout=10)
                for owned_process in alive:
                    owned_process.kill()
                process.wait(timeout=30)
                report.update(status="stopped", stop_reason=reason)
                break
            time.sleep(0.5)
        process.wait(timeout=30)
    report["exit_code"] = process.returncode
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    events = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except ValueError:
            pass
    report["events"] = events
    result = next((e for e in reversed(events) if "result" in e), None)
    if result:
        report.update(result)
        report["status"] = "passed" if result["result"] == "passed" else "failed"
    elif report["status"] == "running":
        report["status"] = "failed"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    emit(report=str(report_path), status=report["status"], seconds=report["elapsed_seconds"],
         peak_gpu_mib=report["gpu_peak_used_mib"], peak_rss_mib=report["peak_process_rss_mib"])
    return 0 if report["status"] == "passed" else 1


def encode_prompt(args, torch):
    from transformers import AutoTokenizer, BitsAndBytesConfig, T5EncoderModel, UMT5EncoderModel
    from diffusers import CogVideoXPipeline, LTXPipeline, WanPipeline
    name = "wan" if args.model == "wan" else "cogvideox"
    folder = MODELS / name
    dtype = torch.bfloat16 if args.model in {"ltx", "wan"} else torch.float16
    # Preserve the checkpoint dtype so CPU tensors can share clean file-mapped
    # pages rather than allocating a second copy of a 10 GB encoder.
    encoder_dtype = torch.bfloat16 if args.model == "wan" else torch.float16
    configuration = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=dtype, llm_int8_enable_fp32_cpu_offload=True,
    )
    encoder_cls = UMT5EncoderModel if args.model == "wan" else T5EncoderModel
    emit(phase="load_text_encoder", precision="nf4" if args.encoder_offload == "nf4" else str(encoder_dtype),
         repository_folder=str(folder), offload=args.encoder_offload)
    if args.encoder_offload == "layer":
        from layer_encoder import load_encoder
        encoder = load_encoder(folder / "text_encoder", encoder_cls, encoder_dtype, torch.device("cuda"))
    elif args.encoder_offload == "nf4":
        encoder = encoder_cls.from_pretrained(
            folder / "text_encoder", torch_dtype=encoder_dtype, local_files_only=True,
            use_safetensors=True, quantization_config=configuration,
            device_map="auto", max_memory={0: "2400MiB", "cpu": "3200MiB"},
            offload_folder=str(OUTPUT / "encoder_offload"),
        )
    else:
        from accelerate import cpu_offload
        encoder = encoder_cls.from_pretrained(
            folder / "text_encoder", torch_dtype=encoder_dtype, local_files_only=True,
            use_safetensors=True, low_cpu_mem_usage=True,
        )
        cpu_offload(encoder, execution_device=torch.device("cuda"))
    tokenizer = AutoTokenizer.from_pretrained(folder / "tokenizer", local_files_only=True)
    pipeline_cls = {"cogvideox": CogVideoXPipeline, "ltx": LTXPipeline, "wan": WanPipeline}[args.model]
    pipe = object.__new__(pipeline_cls)
    pipe.register_modules(text_encoder=encoder, tokenizer=tokenizer, transformer=None, vae=None, scheduler=None)
    kwargs = dict(prompt=PROMPT, negative_prompt=NEGATIVE, do_classifier_free_guidance=True,
                  device=torch.device("cuda"), num_videos_per_prompt=1,
                  max_sequence_length={"cogvideox": 226, "ltx": 128, "wan": 512}[args.model])
    emit(phase="encode_prompt", device_map={k: str(v) for k, v in getattr(encoder, "hf_device_map", {}).items()})
    started = time.monotonic()
    with torch.inference_mode():
        encoded = pipe.encode_prompt(**kwargs)
    if args.model == "ltx":
        positive, positive_mask, negative, negative_mask = encoded
        tensors = dict(prompt_embeds=positive.cpu().to(dtype), prompt_attention_mask=positive_mask.cpu(),
                       negative_prompt_embeds=negative.cpu().to(dtype), negative_prompt_attention_mask=negative_mask.cpu())
    else:
        positive, negative = encoded
        tensors = dict(prompt_embeds=positive.cpu(), negative_prompt_embeds=negative.cpu())
    for key, tensor in tensors.items():
        if not torch.isfinite(tensor).all():
            raise FloatingPointError(f"Nonfinite prompt encoding: {key}")
    torch.save(tensors, OUTPUT / f"{args.model}-prompt.pt")
    return {"result": "passed", "encoding_seconds": round(time.monotonic() - started, 2),
            "embedding_shapes": {k: list(v.shape) for k, v in tensors.items()}}


def load_wan_vae(torch):
    from diffusers import AutoencoderKLWan
    vae = AutoencoderKLWan.from_pretrained(
        MODELS / "wan/vae", torch_dtype=torch.float32,
        local_files_only=True, use_safetensors=True,
    )
    # The first Windows run failed when an idle file-mapped VAE weight was
    # paged in during decode. Keep a real CPU copy before attaching hooks.
    vae._apply(lambda tensor: tensor.clone())
    return vae


def decode_wan(args, torch):
    from accelerate import cpu_offload
    from diffusers.video_processor import VideoProcessor
    saved = torch.load(args.resume_latents, map_location="cpu", weights_only=True)
    for key in ("width", "height", "frames", "steps", "fps"):
        if saved["settings"][key] != getattr(args, key):
            raise ValueError(f"Saved Wan latents have different {key}")
    loaded = time.monotonic()
    vae = load_wan_vae(torch)
    vae.enable_tiling()
    cpu_offload(vae, execution_device=torch.device("cuda"))
    load_seconds = time.monotonic() - loaded
    started = time.monotonic()
    emit(phase="resume_decode", source=str(args.resume_latents), vae="float32", storage="owned_cpu_copy")
    with torch.inference_mode():
        latents = saved["latents"].to(device="cuda", dtype=torch.float32)
        mean = torch.tensor(vae.config.latents_mean, device="cuda", dtype=torch.float32).view(1, vae.config.z_dim, 1, 1, 1)
        inverse_std = 1.0 / torch.tensor(vae.config.latents_std, device="cuda", dtype=torch.float32).view(1, vae.config.z_dim, 1, 1, 1)
        video = vae.decode(latents / inverse_std + mean, return_dict=False)[0]
        frames = VideoProcessor(vae_scale_factor=vae.config.scale_factor_spatial).postprocess_video(video, output_type="np")[0]
    torch.cuda.synchronize()
    decode_seconds = time.monotonic() - started
    result = save_video(args, frames, load_seconds, decode_seconds)
    result.update(decode_seconds=round(decode_seconds, 2), denoising_repeated=False,
                  original_denoising_seconds=saved["denoise_seconds"])
    return result


def generate(args, torch):
    if args.resume_latents:
        if args.model != "wan":
            raise ValueError("Latent recovery is currently supported for Wan only")
        return decode_wan(args, torch)
    from diffusers import (AnimateDiffPipeline, MotionAdapter, DDIMScheduler,
                           CogVideoXPipeline, LTXPipeline, WanPipeline)
    dtype = torch.bfloat16 if args.model in {"ltx", "wan"} else torch.float16
    started = time.monotonic()
    emit(phase="load_video_model", model=args.model)
    if args.model == "animatediff":
        if args.motion_chunk_size < 1 or args.frames % args.motion_chunk_size:
            raise ValueError("The AnimateDiff chunk size must be positive and divide the frame count")
        adapter = MotionAdapter.from_pretrained(MODELS / "animatediff", variant="fp16",
                                                torch_dtype=dtype, local_files_only=True, use_safetensors=True)
        # UNetMotionModel constructs a fresh UNet before copying the frozen
        # weights. Build that temporary model in the requested precision too.
        previous_dtype = torch.get_default_dtype()
        try:
            torch.set_default_dtype(previous_dtype if args.motion_standard_init else dtype)
            if args.sd_base == "realistic":
                from diffusers import StableDiffusionPipeline, AutoencoderKL
                vae = AutoencoderKL.from_pretrained(MODELS / "sd15/vae", torch_dtype=dtype,
                                                    variant="fp16", local_files_only=True,
                                                    use_safetensors=True)
                base = StableDiffusionPipeline.from_single_file(
                    str(MODELS / "realistic/Realistic_Vision_V5.1_fp16-no-ema.safetensors"),
                    config=str(MODELS / "sd15"), vae=vae, safety_checker=None,
                    torch_dtype=dtype, local_files_only=True,
                )
                pipe = AnimateDiffPipeline(vae=base.vae, text_encoder=base.text_encoder,
                    tokenizer=base.tokenizer, unet=base.unet, scheduler=base.scheduler,
                    motion_adapter=adapter)
                del base, vae
            else:
                pipe = AnimateDiffPipeline.from_pretrained(
                    MODELS / "sd15", motion_adapter=adapter, torch_dtype=dtype, variant="fp16",
                    local_files_only=True, use_safetensors=True,
                )
        finally:
            torch.set_default_dtype(previous_dtype)
        # The motion weights have been copied into the combined UNet.
        # Release the original adapter before inference on a small-RAM system.
        del adapter
        import gc
        gc.collect()
        pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config, clip_sample=False,
                                                   timestep_spacing="linspace", steps_offset=1,
                                                   beta_schedule="linear")
        emit(phase="motion_scheduler", beta_schedule="linear")
        pipe.enable_vae_slicing()
        pipe.enable_vae_tiling()
        pipe.unet.enable_forward_chunking(chunk_size=args.motion_chunk_size, dim=1)
        call = dict(prompt=PROMPT, negative_prompt=NEGATIVE, guidance_scale=7.5)
    else:
        call = torch.load(OUTPUT / f"{args.model}-prompt.pt", weights_only=True)
        if args.model == "ltx":
            from safetensors import safe_open
            from diffusers import FlowMatchEulerDiscreteScheduler
            checkpoint_path = MODELS / "ltx/ltxv-2b-0.9.6-distilled-04-25.safetensors"
            with safe_open(checkpoint_path, framework="pt", device="cpu") as checkpoint:
                checkpoint_config = json.loads(checkpoint.metadata()["config"])
            checkpoint_sigmas = checkpoint_config["allowed_inference_steps"]
            if args.steps != len(checkpoint_sigmas):
                raise ValueError("This distilled checkpoint uses its declared eight-step schedule")

            class CheckpointScheduler(FlowMatchEulerDiscreteScheduler):
                def set_timesteps(self, num_inference_steps=None, device=None, sigmas=None,
                                  mu=None, timesteps=None):
                    # v0.35's LTX pipeline always supplies a uniform sigma array.
                    # Preserve the distilled checkpoint's published noise levels.
                    return super().set_timesteps(device=device, sigmas=checkpoint_sigmas, mu=mu)

            pipe = LTXPipeline.from_single_file(
                str(checkpoint_path),
                config=str(MODELS / "ltx-config"), text_encoder=None, tokenizer=None,
                torch_dtype=dtype, local_files_only=True,
            )
            pipe.scheduler = CheckpointScheduler.from_config(pipe.scheduler.config, stochastic_sampling=True)
            emit(phase="distilled_schedule", sigmas=checkpoint_sigmas, stochastic_sampling=True)
            pipe.vae.enable_tiling()
            if args.framewise_vae:
                pipe.vae.use_framewise_decoding = True
            call.update(guidance_scale=1.0, decode_timestep=0.05, decode_noise_scale=0.025,
                        frame_rate=args.fps)
            call.pop("negative_prompt_embeds", None)
            call.pop("negative_prompt_attention_mask", None)
        else:
            if args.model == "wan":
                from diffusers import UniPCMultistepScheduler
                vae = load_wan_vae(torch)
                pipe = WanPipeline.from_pretrained(
                    MODELS / "wan", vae=vae, text_encoder=None, tokenizer=None,
                    torch_dtype=dtype, local_files_only=True, use_safetensors=True,
                )
                pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=3.0)
                if pipe.vae.dtype != torch.float32:
                    raise ValueError("Wan's decoder must retain FP32 precision")
                emit(phase="wan_precision", transformer=str(pipe.transformer.dtype),
                     vae=str(pipe.vae.dtype), flow_shift=3.0)
                del vae
            else:
                pipe = CogVideoXPipeline.from_pretrained(
                    MODELS / "cogvideox", text_encoder=None, tokenizer=None,
                    torch_dtype=dtype, local_files_only=True, use_safetensors=True,
                )
            pipe.vae.enable_tiling()
            if args.model == "cogvideox":
                pipe.vae.enable_slicing()
            call.update(guidance_scale=6.0 if args.model == "cogvideox" else 5.0)
    if args.offload == "group":
        from diffusers.hooks import apply_group_offloading
        denoiser = pipe.unet if args.model == "animatediff" else pipe.transformer
        denoiser.enable_group_offload(
            onload_device=torch.device("cuda"), offload_device=torch.device("cpu"),
            offload_type="block_level", num_blocks_per_group=1, low_cpu_mem_usage=True,
        )
        pipe.vae.enable_group_offload(
            onload_device=torch.device("cuda"), offload_device=torch.device("cpu"),
            offload_type="leaf_level", low_cpu_mem_usage=True,
        )
        if pipe.text_encoder is not None:
            apply_group_offloading(
                pipe.text_encoder, onload_device=torch.device("cuda"),
                offload_device=torch.device("cpu"), offload_type="block_level",
                num_blocks_per_group=1, low_cpu_mem_usage=True,
            )
    elif args.offload == "model":
        pipe.enable_model_cpu_offload()
    else:
        pipe.enable_sequential_cpu_offload()
    pipe.set_progress_bar_config(disable=True)
    load_seconds = time.monotonic() - started
    call.update(width=args.width, height=args.height, num_frames=args.frames,
                num_inference_steps=args.steps, generator=torch.Generator("cpu").manual_seed(42))
    def callback(_pipe, step, _timestep, values):
        if not torch.isfinite(values["latents"]).all():
            raise FloatingPointError(f"Nonfinite video latents at step {step + 1}")
        emit(phase="denoise", step=step + 1, steps=args.steps,
             seconds=round(time.monotonic() - generation_started, 2))
        if args.model == "wan" and step + 1 == args.steps:
            tag = args.tag or f"{args.action}-{args.model}"
            checkpoint = OUTPUT / f"{tag}-latents.pt"
            torch.save({"latents": values["latents"].detach().cpu(),
                        "settings": {k: getattr(args, k) for k in ("width", "height", "frames", "steps", "fps")},
                        "denoise_seconds": round(time.monotonic() - generation_started, 2)}, checkpoint)
            emit(phase="latents_saved", path=str(checkpoint))
        return values
    call["callback_on_step_end"] = callback
    generation_started = time.monotonic()
    emit(phase="generate", width=args.width, height=args.height, frames=args.frames, steps=args.steps)
    with torch.inference_mode():
        frames = pipe(**call).frames[0]
    torch.cuda.synchronize()
    generation_seconds = time.monotonic() - generation_started
    return save_video(args, frames, load_seconds, generation_seconds)


def save_video(args, frames, load_seconds, generation_seconds):
    from diffusers.utils import export_to_video
    tag = args.tag or f"{args.action}-{args.model}"
    path = OUTPUT / f"{tag}.mp4"
    export_to_video(frames, str(path), fps=args.fps)
    # Check the encoded artifact, not just whether the model returned an object.
    import imageio.v2 as imageio
    import numpy as np
    reader = imageio.get_reader(path)
    expected_count = len(frames)
    sample_indices = sorted(set([0, expected_count // 2, expected_count - 1]))
    samples, count, pixels, pixel_sum, square_sum, change_sum = {}, 0, 0, 0.0, 0.0, 0.0
    previous = None
    for index, frame in enumerate(reader):
        frame = np.asarray(frame)
        if index in sample_indices:
            samples[index] = frame.copy()
        count += 1
        pixels += frame.size
        pixel_sum += float(frame.sum(dtype=np.float64))
        square_sum += float(np.square(frame, dtype=np.float64).sum())
        if previous is not None:
            change_sum += float(np.abs(frame.astype(np.int16) - previous.astype(np.int16)).mean())
        previous = frame
    reader.close()
    if count != expected_count or path.stat().st_size < 1024:
        raise RuntimeError("No decodable video was saved")
    mean_change = change_sum / max(1, count - 1)
    pixel_std = max(0.0, square_sum / pixels - (pixel_sum / pixels) ** 2) ** 0.5
    from PIL import Image, ImageDraw
    sheet = Image.new("RGB", (args.width * len(sample_indices), args.height + 24), "white")
    drawing = ImageDraw.Draw(sheet)
    for slot, index in enumerate(sample_indices):
        sheet.paste(Image.fromarray(samples[index]), (slot * args.width, 24))
        drawing.text((slot * args.width + 5, 5), f"Frame {index + 1}", fill="black")
    sheet.save(OUTPUT / f"{tag}-frames.jpg")
    return {"result": "passed", "load_seconds": round(load_seconds, 2),
            "generation_seconds": round(generation_seconds, 2), "artifact": str(path),
            "frames_decoded": count, "output_bytes": path.stat().st_size,
            "pixel_standard_deviation": round(pixel_std, 3),
            "mean_adjacent_frame_change": round(mean_change, 3)}


def image_reference(args, torch):
    """Check the base SD model separately when motion outputs look incorrect."""
    from diffusers import StableDiffusionPipeline, AutoencoderKL
    started = time.monotonic()
    if args.sd_base == "realistic":
        vae = AutoencoderKL.from_pretrained(MODELS / "sd15/vae", torch_dtype=torch.float16,
                                            variant="fp16", local_files_only=True, use_safetensors=True)
        pipe = StableDiffusionPipeline.from_single_file(
            str(MODELS / "realistic/Realistic_Vision_V5.1_fp16-no-ema.safetensors"),
            config=str(MODELS / "sd15"), vae=vae, safety_checker=None,
            torch_dtype=torch.float16, local_files_only=True,
        )
        del vae
    else:
        pipe = StableDiffusionPipeline.from_pretrained(
            MODELS / "sd15", torch_dtype=torch.float16, variant="fp16",
            local_files_only=True, use_safetensors=True, safety_checker=None,
        )
    pipe.enable_model_cpu_offload()
    pipe.enable_vae_slicing()
    pipe.enable_vae_tiling()
    pipe.set_progress_bar_config(disable=True)
    with torch.inference_mode():
        picture = pipe(prompt=PROMPT, negative_prompt=NEGATIVE, width=args.width,
                       height=args.height, num_inference_steps=args.steps,
                       generator=torch.Generator("cpu").manual_seed(42)).images[0]
    path = OUTPUT / f"{args.tag or 'sd15-reference'}.png"
    picture.save(path)
    return {"result": "passed", "artifact": str(path),
            "generation_seconds": round(time.monotonic() - started, 2)}


def worker(args):
    import faulthandler
    faulthandler.enable()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      TOKENIZERS_PARALLELISM="false", HF_HOME=str(OUTPUT / "huggingface"))
    import torch
    torch.set_num_threads(4)
    torch.manual_seed(42)
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        torch.cuda.reset_peak_memory_stats()
        if args.action == "probe":
            from diffusers import AnimateDiffPipeline, CogVideoXPipeline, LTXPipeline, WanPipeline
            import bitsandbytes
            matrix = torch.randn((512, 512), device="cuda")
            value = (matrix @ matrix).mean().item()
            result = {"result": "passed", "torch": torch.__version__, "cuda": torch.version.cuda,
                      "gpu": torch.cuda.get_device_name(), "bitsandbytes": bitsandbytes.__version__,
                      "cuda_matmul_finite": bool(torch.isfinite(torch.tensor(value)))}
        elif args.action == "encode":
            result = encode_prompt(args, torch)
        elif args.action == "image-reference":
            result = image_reference(args, torch)
        else:
            result = generate(args, torch)
    except Exception as exc:
        traceback.print_exc()
        result = {"result": "failed", "error_type": type(exc).__name__, "error": str(exc)[:1500]}
    if torch.cuda.is_available():
        result.update(cuda_peak_allocated_mib=round(torch.cuda.max_memory_allocated() / 2**20),
                      cuda_peak_reserved_mib=round(torch.cuda.max_memory_reserved() / 2**20))
    emit(**result)
    return 0 if result["result"] == "passed" else 1


def main():
    global MODELS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["probe", "encode", "generate", "image-reference"])
    parser.add_argument("--model", choices=["animatediff", "cogvideox", "ltx", "wan"], default="animatediff")
    parser.add_argument("--tag")
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--frames", type=int, default=9)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--offload", choices=["sequential", "model", "group"], default="sequential")
    parser.add_argument("--encoder-offload", choices=["sequential", "nf4", "layer"], default="layer")
    parser.add_argument("--sd-base", choices=["sd15", "realistic"], default="sd15")
    parser.add_argument("--motion-chunk-size", type=int, default=1)
    parser.add_argument("--motion-standard-init", action="store_true")
    parser.add_argument("--resume-latents", type=Path)
    parser.add_argument("--framewise-vae", action="store_true")
    parser.add_argument("--models-root", type=Path, default=MODELS)
    parser.add_argument("--min-available-ram", type=int, default=768)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    MODELS = args.models_root.resolve()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    return worker(args) if args.worker else run_monitored(args)


if __name__ == "__main__":
    raise SystemExit(main())
