"""One offline generation per subprocess. No server, downloads, or cloud API calls."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def emit(**values):
    print(json.dumps(values), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--request")
    parser.add_argument("--model-path")
    parser.add_argument("--output")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--config-path")
    parser.add_argument("--text-path")
    parser.add_argument("--offload-path")
    parser.add_argument("--min-available-ram-mb", type=int, default=768)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if args.probe:
        available = all(importlib.util.find_spec(name) for name in ("torch", "diffusers", "transformers", "imageio"))
        if not available:
            emit(available=False, cuda=False, reason="Install requirements-local-media.txt in the media environment.")
        else:
            try:
                import torch
                import diffusers
                from diffusers import AutoPipelineForText2Image, LTXPipeline  # noqa: F401
                import imageio_ffmpeg
                imageio_ffmpeg.get_ffmpeg_exe()
                cuda = torch.cuda.is_available()
                emit(available=True, cuda=cuda, torch=torch.__version__,
                     bf16=cuda and torch.cuda.is_bf16_supported(),
                     ltx=diffusers.__version__ == "0.35.2" and bool(importlib.util.find_spec("psutil")))
            except Exception as exc:
                emit(available=False, cuda=False, reason=f"Media libraries could not load: {type(exc).__name__}.")
        return
    if not all((args.request, args.model_path, args.output)):
        parser.error("--request, --model-path and --output are required")
    spec = json.loads(Path(args.request).read_text(encoding="utf-8"))
    import torch
    from diffusers import AutoPipelineForText2Image
    from diffusers.utils import export_to_video

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("The media environment does not have a usable CUDA runtime.")
    dtype = torch.float32 if device == "cpu" else torch.float16
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    torch.manual_seed(spec["seed"])
    emit(progress=5, message="Loading local model")
    prompt = f"{spec['prompt'].strip()}. Visual style: {spec['style'].strip()}."
    model_path = str(Path(args.model_path).resolve())
    generator = torch.Generator(device="cpu").manual_seed(spec["seed"])

    def progress_callback(_pipeline, step, _timestep, kwargs):
        emit(progress=20 + round(65 * (step + 1) / steps), message=f"Generating step {step + 1}/{steps}")
        return kwargs

    if spec["kind"] == "image":
        steps = 2
        pipe = AutoPipelineForText2Image.from_pretrained(model_path, torch_dtype=dtype, variant="fp16",
                                                       local_files_only=True, use_safetensors=True)
        pipe.enable_attention_slicing()
        pipe.enable_vae_slicing()
        if device == "cuda":
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cpu")
        image = pipe(prompt=prompt, width=spec["width"], height=spec["height"],
                     num_inference_steps=steps, guidance_scale=0.0, generator=generator,
                     callback_on_step_end=progress_callback).images[0]
        image.save(args.output, format="PNG")
    else:
        if device != "cuda" or not torch.cuda.is_bf16_supported():
            raise RuntimeError("LTX video previews require a CUDA GPU with BF16 support.")
        if not args.config_path or not args.text_path or not args.offload_path:
            raise ValueError("--config-path, --text-path and --offload-path are required for LTX video.")
        import psutil
        from services.local_agent.video_pipeline import generate
        # Stop before sustained system memory pressure exhausts Windows' commit
        # budget. The API reads this event and removes any partial output.
        def memory_guard():
            low_samples = 0
            while True:
                available = psutil.virtual_memory().available / 1024**2
                low_samples = low_samples + 1 if available < args.min_available_ram_mb else 0
                if low_samples >= 10:
                    emit(error="Video stopped because system RAM is low. Close other applications or try a shorter preview.")
                    os._exit(3)
                time.sleep(0.5)
        threading.Thread(target=memory_guard, daemon=True).start()
        video = generate(spec, Path(model_path), Path(args.config_path).resolve(), Path(args.text_path).resolve(),
                         Path(args.offload_path).resolve(), emit)
        emit(progress=95, message="Saving MP4 preview")
        export_to_video(video, args.output, fps=spec["fps"])
        import imageio.v2 as imageio
        reader = imageio.get_reader(args.output)
        try:
            count = sum(1 for _frame in reader)
        finally:
            reader.close()
        expected = ((spec["seconds"] * spec["fps"] - 1 + 7) // 8) * 8 + 1
        if count != expected:
            raise RuntimeError("The saved video does not contain all generated frames.")
    emit(progress=100, message="Complete", complete=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Only explicit resource/input errors are shared with the app. Full
        # tracebacks stay in the local owner-scoped worker log.
        emit(error=str(exc)[:300] if isinstance(exc, (ValueError, FloatingPointError)) else
             "The local model could not generate an output. Check the worker log and available memory.")
        raise
