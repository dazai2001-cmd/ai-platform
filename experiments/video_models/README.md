# Local video model experiments

These are standalone, real-weight inference experiments. They do not register new
Workspace models or change the API's existing CPU media environment.

Create a separate environment and install the official CUDA runtime:

```powershell
.\.venv\Scripts\python.exe -m venv .venv-video-test
.\.venv-video-test\Scripts\python.exe -m pip install --no-cache-dir torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv-video-test\Scripts\python.exe -m pip install --no-cache-dir -r requirements-local-video-test.txt
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py probe --tag cuda-probe
```

Model revisions and file sizes are recorded in the ignored
`data/cache/video_benchmark/model_inventory.json`. Download only the declared
checkpoint files using the Hugging Face CLI. Generation runs offline with
`local_files_only=True`; it does not provision remote compute.

All attempts use the same red toy car prompt and seed 42. The default first test
is 256 x 256, nine frames at 8 fps, and eight denoising steps. This is a memory
and execution test, below the models' usual training resolution and duration;
it cannot establish normal-resolution quality. Higher-quality follow-ups use
separate tags and record their actual configuration.

Each model runs in a separate child process. The parent records process memory,
system RAM headroom, total GPU memory sampled with `nvidia-smi`, wall time, and
the worker's CUDA allocator peaks. MP4s are reopened and decoded; sample frames
and adjacent-frame differences are saved to help inspect blank or static output.
A completed MP4 is an execution pass, not a visual-quality score.

Examples after downloading the local checkpoints (run one GPU command at a time):

```powershell
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py generate --model animatediff --sd-base realistic --width 512 --height 512 --frames 16 --steps 25 --offload group --motion-standard-init --motion-chunk-size 8 --tag animatediff-standard-control
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py encode --model cogvideox --tag cogvideox-encode-layer
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py generate --model cogvideox --width 720 --height 480 --frames 9 --steps 25 --offload sequential --tag cogvideox-native-short
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py encode --model ltx --tag ltx-encode-layer
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py generate --model ltx --width 704 --height 512 --frames 161 --steps 8 --fps 25 --framewise-vae --tag ltx-native-framewise
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py encode --model wan --models-root D:\ai-platform-video-benchmark-20261009 --tag wan-encode-layer
.\.venv-video-test\Scripts\python.exe experiments/video_models/benchmark.py generate --model wan --models-root D:\ai-platform-video-benchmark-20261009 --width 832 --height 480 --frames 33 --steps 30 --fps 16 --tag wan-native-resident
```

CogVideoX and LTX both declare the same `google/t5-v1_1-xxl` encoder architecture.
The benchmark reuses CogVideoX's FP16 encoder weights for these two models.
The default encoder mode preserves that precision and loads one transformer
block at a time from the real checkpoint. Embedding lookup reads the requested
rows from the original table. `encoder_self_check.py` compares both T5 and UMT5
against ordinary encoders, including a repeated forward pass, and checks that
blocks are released. This avoids the large RAM copies encountered with ordinary
and NF4 loaders on this laptop. Those modes remain available with
`--encoder-offload sequential` and `--encoder-offload nf4`.
Prompt encoding runs in a separate process so the encoder is released before
video generation. Wan uses its own UMT5 encoder. Diffusion weights stay FP16 or BF16; sequential,
whole-model, and block group CPU offloading can be compared with `--offload`.
These choices are recorded in the attempt logs. Only one GPU worker runs at a
time. To stop an attempt, write a reason to `<tag>.stop` in the output folder;
the parent then stops its own complete worker tree and records the reason.

The LTX candidate is the official **2B 0.9.6 distilled** single-file checkpoint,
loaded with the compatible 0.9.5 Diffusers component configuration. It is not the
13B checkpoint from the similarly named newer Diffusers repository.
Its declared eight sigma levels are read from the checkpoint metadata and used
with stochastic sampling, guidance 1, and the published decoder settings. These
match the [official distilled configuration](https://github.com/Lightricks/LTX-Video/blob/main/configs/ltxv-2b-0.9.6-distilled.yaml).
The `--framewise-vae` option enables LTX's framewise decoder, which reduced the
measured decoder allocation peak in the completed six-second test.

AnimateDiff uses the official motion adapter and either original SD 1.5 or
RealisticVision V5.1 as its image base. Its DDIM scheduler must use a **linear**
beta schedule, as in the [official example](https://huggingface.co/docs/diffusers/v0.35.0/en/api/pipelines/animatediff).
Earlier attempts that inherited SD 1.5's scaled-linear schedule are excluded
from quality conclusions. RealisticVision uses the separately downloaded SD VAE.
The default motion initialization uses FP16 to avoid a large temporary FP32
UNet copy. `--motion-standard-init` retains the library's ordinary construction
for a control run. `--motion-chunk-size` controls feed-forward chunking; the
value must divide the frame count and the attention sequence dimensions.

The official Wan Diffusers checkpoint includes FP32 weights. `prepare_wan.py`
downloads one shard at a time and converts its transformer weights to BF16 using
`convert_checkpoint.py`, with small row chunks. It validates the new safetensors
header before deleting only that task-owned original shard. Conversion was
checked with matrices, scalar tensors, integer tensors, and empty tensors.
The VAE stays in its original **FP32** precision, following the
[official Wan recipe](https://huggingface.co/docs/diffusers/v0.35.0/en/api/pipelines/wan).
Wan generation explicitly supplies that FP32 VAE and uses flow shift 3 for 480P.
It clones the decoder weights into ordinary CPU RAM before offloading; the
initial Windows run crashed while paging an idle mapped decoder weight in.
The last denoising callback saves `<tag>-latents.pt` before decoding. To retry
only Wan decoding, use the same width, height, frame count, steps and fps with
`--resume-latents data/cache/video_benchmark/<tag>-latents.pt` and a new output
tag. Such a report records decoder time and the original denoising time
separately; it must not be presented as a fresh full generation time.
The completed recovery test decoded the saved 33-frame latents in 73.91 s and
produced the same MP4 SHA256 as the full run.

Alternatively, download the official Wan native BF16 UMT5 checkpoint and run
`convert_wan_encoder.py`. This losslessly renames its keys using the published
Wan module definitions, checks every key and shape against UMT5, verifies the
source SHA256 and endpoint values of each output tensor, and writes safetensors.
It halves the encoder download compared with the FP32 Diffusers encoder.
The converter releases the native file mapping before opening the output for
validation, retaining only small cloned endpoint samples. This avoids charging
two large private mappings against Windows' paging-file budget at once.

Wan's task-owned checkpoints are stored on `D:\ai-platform-video-benchmark-20261009`
to preserve free space on C. That directory contains `benchmark-owner.json`;
`data/cache/video_benchmark/model-locations.json` records the location. The other
models are under `data/models/video-benchmark`. For a new machine, use a suitable
directory and pass it consistently with `--models-root`.

Prepare Wan **after** converting its native encoder so the preparer skips the
larger FP32 encoder download:

```powershell
.\.venv-video-test\Scripts\python.exe experiments/video_models/convert_wan_encoder.py D:\ai-platform-video-benchmark-20261009\wan-native\models_t5_umt5-xxl-enc-bf16.pth --models-root D:\ai-platform-video-benchmark-20261009 --remove-source
.\.venv-video-test\Scripts\python.exe experiments/video_models/prepare_wan.py --models-root D:\ai-platform-video-benchmark-20261009
```

To reclaim disk space, the CogVideoX T5 weight files were removed after both
CogVideoX and LTX prompt encodings completed. Their configs and the fixed test
prompt's embeddings remain. Generation can reuse those embeddings; encoding
any new prompt requires downloading the encoder weights again. This experiment
does not supply a general prompt-serving backend from those stored embeddings.

Attempts have a configurable time limit (default 20 minutes). A run is stopped
if available physical RAM stays below its configured reserve (default 768 MiB)
for five seconds. A stop is reported explicitly and is not called a successful
generation or proof that the model cannot run with other configurations.

Keep outputs and full attempt JSON/logs under `data/cache/video_benchmark`.
The final findings are summarized in `docs/video-model-benchmark.md`.
