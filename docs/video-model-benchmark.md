# Local video model benchmark

Test date: 9 October 2026. Status: **completed**. All four candidates ran actual
inference; results include the successful and failed configurations below.

LTX-Video 2B distilled is the most practical completed candidate on this laptop.
It generated a 6.44-second, 704×512 clip in 87 seconds using framewise decoding.
The subject is recognizable, but its geometry is odd and it is mostly stationary.
CogVideoX produced a clearer car in its short test; the six-second attempt
stopped at the physical RAM reserve. These are results from one fixed prompt.
Wan also completed a native-resolution short clip, with a clearer toy car but
roughly thirteen minutes of generation for two seconds of output. AnimateDiff
ran at native resolution but did not produce useful imagery in this setup.
LTX is the first candidate to consider for experimental local previews. This
test did not establish reliable motion or prompt-following quality for any model.

## Results

| Candidate / attempt | Resolution, duration, steps | Generation time | Peak worker RAM | CUDA allocated / reserved peak | Outcome and inspected quality |
| --- | --- | --- | --- | --- | --- |
| LTX-Video 2B 0.9.6 distilled, framewise decoder | 704×512, 161 frames at 25 fps (6.44 s), 8 | 87.23 s | 7,623 MiB | 1,170 / 1,840 MiB | Playable MP4; recognizable red toy truck/car, malformed double-ended shape, mostly stationary. |
| LTX, ordinary tiled decoder | Same settings | 114.17 s | 8,275 MiB | 4,073 / 5,538 MiB | Playable MP4; similar quality, larger decoder allocation. |
| CogVideoX-2B, short clip | 720×480, 9 frames at 8 fps (1.125 s), 25 | 113.11 s | 6,062 MiB | 3,234 / 3,720 MiB | Playable MP4; clear toy car on wood, very little movement. |
| CogVideoX-2B, full clip | 720×480, 49 frames at 8 fps (6.125 s), 30 | Stopped after step 1 | 5,633 MiB | Worker stopped before final measurement | RAM reserve stop at 43.89 s wall time; available RAM reached 493 MiB. No saved clip. |
| AnimateDiff + RealisticVision V5.1, reduced resolution | 256×256, 16 frames at 8 fps (2 s), 25 | 252.38 s | 5,803 MiB | 2,871 / 3,264 MiB | Playable MP4; reddish blur without a clear subject. Below usual training resolution. |
| AnimateDiff + RealisticVision V5.1, native resolution | 512×512, 16 frames at 8 fps (2 s), 25 | 1,088.95 s | 5,853 MiB | 1,758 / 3,388 MiB | Playable MP4, but still reddish blur without a recognizable car. Correct linear schedule, group offload. |
| AnimateDiff, ordinary initialization control with 8-token chunks | Same resolution, duration and steps | 245.39 s | 8,238 MiB | 1,807 / 2,902 MiB | Playable MP4, still reddish blur. Faster chunking did not improve usefulness. |
| Wan 2.1 T2V 1.3B, mapped decoder | 832×480, 33 frames at 16 fps (2.0625 s), 30 | 666.62 s denoising; no completed clip | 4,800 MiB | Native process crashed before final measurement | All steps completed; Windows native page error in decoder weight transfer. No MP4. |
| Wan, ordinary CPU decoder copies | Same resolution, duration and steps | 761.28 s | 5,060 MiB | 1,556 / 1,870 MiB | Playable MP4. Clear red toy car with leftward drift despite the rightward prompt; invented cartoon eyes. |

Generation time excludes imports, model loading, downloads and prompt encoding.
Total wall time was 105.28 s for LTX framewise and 131.16 s for CogVideoX short.
T5 prompt encoding added about 18 s of encoding plus process/import overhead.
Every new prompt needs its own encoding.
Wan's successful retry took 777.75 s wall time; its denoising took 686.58 s and
decoding about 75 s. UMT5 encoding added 26.48 s plus process/import overhead.
Its 81-frame, 50-step full-length configuration was not tested.

Samples:

- [LTX six-second clip](../data/cache/video_benchmark/ltx-native-framewise.mp4)
  and [first/middle/last frames](../data/cache/video_benchmark/ltx-native-framewise-frames.jpg).
- [CogVideoX short clip](../data/cache/video_benchmark/cogvideox-native-short.mp4)
  and [first/middle/last frames](../data/cache/video_benchmark/cogvideox-native-short-frames.jpg).
- [AnimateDiff native-resolution clip](../data/cache/video_benchmark/animatediff-realistic-native.mp4)
  and [first/middle/last frames](../data/cache/video_benchmark/animatediff-realistic-native-frames.jpg).
- [AnimateDiff control clip](../data/cache/video_benchmark/animatediff-standard-control.mp4)
  and [first/middle/last frames](../data/cache/video_benchmark/animatediff-standard-control-frames.jpg).
- [Wan short clip](../data/cache/video_benchmark/wan-native-resident.mp4)
  and [first/middle/last frames](../data/cache/video_benchmark/wan-native-resident-frames.jpg).

## Hardware, measurement and scope

RTX 3050 Laptop GPU, 4,096 MiB VRAM; 15.71 GiB physical RAM; Windows; NVIDIA
616.92; PyTorch 2.8.0+cu128; Diffusers 0.35.2; Transformers 4.57.6.
The isolated `.venv-video-test` environment keeps the app's CPU media environment
separate. Qwen was absent from GPU memory at the initial check; simultaneous
Qwen orchestration and generation have not been benchmarked. No app integration
or hosted deployment is included.

Worker RAM measures the complete child process tree's resident memory. CUDA
allocator peaks differ from physical GPU use under Windows WDDM: allocations
may spill to shared system memory, and GPU samples may miss short peaks.
Sampled total GPU peaks, including desktop use, were 2,083 MiB for LTX framewise
and 2,065 MiB for CogVideoX short. These are different measurements, not
interchangeable VRAM requirements.
Sampled total GPU peaks were 3,579 MiB for the AnimateDiff control and 2,502 MiB
for Wan's completed retry. Wan retained at least 4,792 MiB available system RAM.

The monitor samples RAM every 0.5 s and GPU use every 5 s. It stops its own
worker tree if available physical RAM stays below 768 MiB for 5 s, or if the
attempt exceeds its time limit. A stopped run does not establish that a model
cannot run with more available RAM or different settings. User apps were not closed.

## Method and model settings

All video tests use seed 42 and this prompt:

> A small red toy car drives slowly from left to right across a wooden table.
> Static camera, soft daylight, realistic photography, smooth continuous movement.

Negative prompt: `blurry, distorted, flickering, low quality`. Initial 256×256,
nine-frame smoke tests check execution, not quality at ordinary resolution.
The table records follow-up settings. Saved MP4s are reopened and every frame
decoded; subject clarity and motion are judged separately from frame sheets.
Different durations, resolutions and step counts prevent a controlled ranking.
An auxiliary red-pixel mask measured horizontal centroids in the first, middle
and last frames: Wan shifted about 44 pixels left; LTX changed by about half
a pixel horizontally; CogVideoX had no measured horizontal centroid change.
This is a simple supporting heuristic, sensitive to shape and color changes,
not optical flow or a general motion-quality metric. Its samples are saved in
`data/cache/video_benchmark/red-mask-motion.json`.

Prompt encoding runs separately from diffusion. The bounded T5/UMT5 loader
reads actual checkpoint blocks one at a time, releases each after use, and
looks up selected rows in the original embedding table. Tiny T5 and UMT5
comparisons against ordinary encoders produced identical outputs, including
repeated masked forwards. That validates the loader mechanism, not broad quality.

- **LTX:** Official 2B 0.9.6 distilled checkpoint with compatible 0.9.5 configs;
  BF16 diffusion, sequential CPU offload, eight sigma values from checkpoint
  metadata, stochastic sampling, guidance 1, decoder timestep 0.05 and noise
  scale 0.025. Uses the [official distilled recipe](https://github.com/Lightricks/LTX-Video/blob/main/configs/ltxv-2b-0.9.6-distilled.yaml).
  The framewise follow-up enables the installed VAE's framewise decoder.
- **CogVideoX:** Official 2B FP16 diffusion weights; sequential CPU offload;
  tiled/sliced VAE; guidance 6. CogVideoX and LTX use the compatible CogVideoX
  FP16 T5-v1.1-XXL encoder, retaining FP32 protected projections.
- **AnimateDiff:** Official v1.5-2 motion adapter; FP16 RealisticVision V5.1
  SD 1.5 base with a separate SD VAE; DDIM **linear** beta schedule, linspace
  timesteps, guidance 7.5; chunked UNet, VAE tiling/slicing. Native test uses
  group CPU offload, following the [official example](https://huggingface.co/docs/diffusers/v0.35.0/en/api/pipelines/animatediff).
- **Wan:** Official 1.3B transformer converted FP32→BF16 in bounded chunks;
  original FP32 VAE preserved; native BF16 UMT5 encoder losslessly renamed to
  HF keys. Guidance 5, flow shift 3 for 480P, tiled VAE. FP32 VAE follows the
  [official recipe](https://huggingface.co/docs/diffusers/v0.35.0/en/api/pipelines/wan).

## Earlier attempts and exclusions

Ordinary and NF4 CogVideoX encoder loaders hit the RAM reserve. The bounded
loader subsequently encoded the real prompts with roughly 2 GiB worker RAM.
These loader stops are separate from diffusion results.
Wan's first encoder conversion hit a Windows paging-file limit while mapping
both large files. Releasing the source mapping before output validation fixed
that conversion. Its first encoding process exited with a native access violation
without a Python error or RAM-guard stop; the diagnostic retry completed in
26.48 s of encoding (41.27 s wall time), using 1,772 MiB worker RAM and a
596 MiB CUDA allocation peak. The original crash's cause remains unknown.

Two early native AnimateDiff runs overlapped during a retry and are marked
excluded in their JSON reports. Early AnimateDiff tests also inherited SD's
scaled-linear schedule and are excluded from quality conclusions. Later tests
use the official linear schedule. The correctly scheduled 256×256 RealisticVision
clip still looked poor, prompting the 512×512 follow-up. A separate SD 1.5 image
reference produced a clear car in 7.58 s including loading; it checks the base
image weights independently, not motion quality.
The RealisticVision image base also produced a recognizable car at 512×512
in 9.56 s including loading, using the same single-file loader and separate VAE.
The ordinary initialization control produced the same poor motion output with
eight-token feed-forward chunks. It completed in 276.02 s wall time, peaking at
8,238 MiB worker RAM during construction. Increasing the chunk size reduced
denoising time; the FP16 initialization shortcut did not explain the blur.

Wan's first 480P generation completed all 30 steps with finite latents, then
failed with a native Windows page error in Accelerate's decoder weight transfer.
The VAE file's SHA256 was verified again and still matched the official source.
The retry uses ordinary CPU copies of all FP32 VAE weights, and saves final
latents so a subsequent decoder-only retry does not repeat denoising.
That retry completed all 33 decoded frames. This demonstrates a working
configuration after the mapped-weight transfer failure; it is not an extended
stability test across prompts and sessions.
Decoder-only recovery from the saved latents also completed: 73.91 s decoding,
88.45 s wall time, 2,014 MiB worker RAM and 1,530 MiB peak CUDA allocation.
Its MP4 SHA256 exactly matches the full run's MP4, confirming recovery preserves
the encoded video without repeating the 686.58 s denoising stage.
The encoder key mapping follows [Wan's native implementation](https://raw.githubusercontent.com/Wan-Video/Wan2.1/main/wan/modules/t5.py).
Unscaled attention, per-block relative bias, gated GELU and RMS normalization
were inspected against the installed HF UMT5 code. This is a source-level
structure check; a full native-versus-HF XXL forward parity test was not run.

The tiny CogVideoX smoke clip was nearly blank. LTX's 49-frame preview had an
inconsistent subject. Native-resolution follow-ups are used for conclusions.

## Reproduce and saved files

See [experiment instructions](../experiments/video_models/README.md) and
`requirements-local-video-test.txt`. Revision IDs, sizes and source checksums
are recorded under `data/cache/video_benchmark`, alongside JSON measurements,
logs, MP4s and frame sheets. Inference uses local checkpoints offline.

Wan checkpoints are in the task-owned `D:\ai-platform-video-benchmark-20261009`
directory to preserve free C drive space. Others are under
`data/models/video-benchmark`; paths are recorded in experiment metadata.
CogVideoX's T5 weights were removed after both CogVideoX/LTX benchmark encodings
completed to reclaim disk space. Stored benchmark embeddings cover this fixed
prompt only. The subsequent Workspace integration restored the verified T5
encoder on D: and encodes every new user prompt; see [local workflows](local-workflows.md).
