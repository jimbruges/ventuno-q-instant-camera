# Instant Duck Camera

An App Lab instant camera for VENTUNO Q. The left Modulino button or the web shutter captures the USB webcam, adds a rubber duck with a local generative model, and stores a 200-pixel-wide RGB image at the webcam's native aspect ratio in the camera roll.

## Backends

- `npu`: the default, using FP16 InstructPix2Pix and IP-Adapter Plus on the VENTUNO Q HTP/NPU. It accepts a captured image, an optional reference image, and a flexible instruction, with no user-supplied mask or manual compositing.
- `local`: CPU fallback using Realistic Vision 5.1 (SD 1.5) and the four-step Hyper-SD adapter for img2img.
- `identity`: the slower original mode, using SDXL, PhotoMaker v1, and `assets/reference/me.jpg`.
- `openrouter`: OpenRouter's image editing API. Set `OPENROUTER_API_KEY` in App Lab Brick Configuration, never in source.
- `preview`: deterministic local composite for testing capture, hardware, and UI without model weights.

Copy `config.example.json` to `config.json` to override defaults. `config.json` is optional. The same options are available as uppercase environment variables, including `CAMERA_BACKEND`, `CAMERA_DEVICE`, `CAMERA_PROMPT`, `CAMERA_ROTATION`, `NPU_URL`, `NPU_PROMPT`, `NPU_STEPS`, `NPU_GUIDANCE_SCALE`, `NPU_IMAGE_GUIDANCE_SCALE`, `SD_CLI`, `SD_MODEL`, `SD_LORA`, `GENERATION_WIDTH`, `GENERATION_STEPS`, `GENERATION_STRENGTH`, `IDENTITY_MODEL`, `PHOTO_MAKER_MODEL`, and `OPENROUTER_IMAGE_MODEL`.

The web interface exposes the NPU instruction, optional reference-image upload, source resolution, denoising steps, text guidance, image guidance, and random or fixed seed. The uploaded reference is stored at `assets/reference/object.jpg`; removing it restores prompt-only editing. The IP-Adapter strength is fixed at the compiled model's validated value of 0.8. Applied values take effect on the next exposure and remain active until the App restarts. Source resolution controls preprocessing detail; the compiled editor graph always executes at 512x512.

The local model files are intentionally outside the App directory under `~/instant-camera-ai` so App Lab does not package several gigabytes of weights.

## Installed local stack

- `~/instant-camera-ai/src/instruct_pix2pix_generate.py`: NumPy InstructPix2Pix pipeline using ONNX Runtime QNN.
- `~/instant-camera-ai/src/npu_server.py`: persistent HTTP service exposing the `/edit` image-and-instruction endpoint.
- `~/instant-camera-ai/qnn-runtime`: ONNX Runtime 1.24.4 and `onnxruntime-qnn` 2.1.1, which bundles QNN 2.45.41.
- `~/instant-camera-ai/qnn-runtime-deps`: user-local `libatomic.so.1` and the `libcdsprpc.so` compatibility linker name required by the HTP v75 stub.
- `~/instant-camera-ai/models/qcs8275-sd15`: Qualcomm's precompiled QCS8275 Stable Diffusion 1.5 model contexts.
- `~/instant-camera-ai/models/qcs8275-instruct-pix2pix`: custom FP16 QNN VAE encoder and eight-channel editor U-Net contexts.
- `~/instant-camera-ai/models/qcs8275-ip-adapter-plus`: custom FP16 QNN CLIP Vision reference encoder and IP-Adapter-aware editor U-Net contexts.
- `~/instant-camera-ai/bin/sd-cli`: ARM64 CPU build of `stable-diffusion.cpp`.
- `~/instant-camera-ai/models/realistic-vision-v5.1.safetensors`: compact SD 1.5 base model.
- `~/instant-camera-ai/models/hyper-sd15-4step.safetensors`: four-step Hyper-SD adapter.
- `~/instant-camera-ai/models/sdxl-lightning.safetensors`: DreamShaper XL Lightning.
- `~/instant-camera-ai/models/photomaker-v1.safetensors`: PhotoMaker v1 identity adapter.
- `tools/ffmpeg`: bundled static ARM64 webcam capture binary.

The NPU editor uses 20 Euler ancestral steps with separate text and image guidance. A verified 512x512 edit completed in 25.2 seconds. Warm component timings were 310 ms for source encoding and about 400 ms for each editor U-Net pass; each step performs three U-Net passes. The CPU local profile remains available and took about 1 minute 50 seconds with a 2.5 GiB peak. The identity profile took about six minutes with an 8.1 GiB peak.

GenieX is not used because its public runtime supports LLM and VLM inference rather than Stable Diffusion graphs. This backend uses Qualcomm's dedicated QNN Stable Diffusion export and `/dev/fastrpc-cdsp` directly through ONNX Runtime QNN.

Preprocessing preserves the webcam frame's native aspect ratio without padding, borders, or destructive center crops. Captures are not rotated by default; set `camera_rotation` to `90`, `-90`, or `180` only if the camera is mounted differently.

The Vulkan build detects the Adreno623, but Mesa Turnip rejects a generated compute shader because its workgroup barrier cannot schedule enough concurrent waves. The verified QNN backend avoids that Vulkan path.

The camera roll includes deterministic preview fixtures and outputs produced by the installed local models. OpenRouter support accepts either `message.images` or image items in the response content array.