# Instant Me Photobooth

An App Lab instant camera and thermal-printing photobooth for VENTUNO Q. It can run headless from Modulino Buttons while the Web UI provides configuration, test captures, test prints, and recent-print history.

## Headless controls

- Button A takes an unmodified photograph, archives it, and prints it.
- Button B sends the captured scene and `assets/reference/me.jpg` to OpenRouter's `google/gemini-2.5-flash-image` (Nano Banana), asking it to place the reference person into the scene, then archives and prints the result.
- Button C applies the configured local VENTUNO Q NPU edit, then archives and prints the result.

A button LED is steady only while that mode is available. Button B remains dark unless the camera, reference portrait, `OPENROUTER_API_KEY`, and internet connection are available. Button C remains dark if the local NPU endpoint cannot be reached. The selected button flashes while a job runs. The LED matrix shows ready, capture, processing/printing, success, and error states; Modulino Pixels provide the shutter flash.

## Thermal printer wiring

The printer is driven at 19200 baud through `Serial1`:

- VENTUNO Q D1 / TX to printer RX.
- VENTUNO Q GND to printer GND.
- Printer power to a separate regulated 5-9 V supply rated for at least 1.5 A.
- Keep printer TX disconnected. If status receive is added later, level-shift it from 5 V to 3.3 V before connecting to VENTUNO Q D0 / RX.

Never power the printer from the board's 3.3 V or 5 V logic header. The external supply and VENTUNO Q must share ground. Start with the default heat and density values; overly aggressive settings increase current demand and can overheat the print head.

The Linux app converts each result to a 384-dot, one-bit raster and sends up to eight rows per acknowledged Bridge call. A rejected transfer is cancelled on the MCU so later print jobs are not blocked.

## Bring-up

1. Power the printer externally with paper loaded, common ground connected, and printer TX disconnected.
2. Start the App from App Lab. This uploads the MCU sketch and stops any currently running App.
3. Check the App logs for Python startup errors and the MCU monitor for sketch output.
4. Use **Test Print** in the Web UI before enabling print-after-capture.
5. Trigger A, then C, then B after configuring `OPENROUTER_API_KEY` in Brick Configuration.

The build and mocked transport tests do not energize the printer. A real test print requires the physical wiring above.

## Backends

- `npu`: the default, using FP16 InstructPix2Pix and IP-Adapter Plus on the VENTUNO Q HTP/NPU. It accepts a captured image, an optional reference image, and a flexible instruction, with no user-supplied mask or manual compositing.
- `local`: CPU fallback using Realistic Vision 5.1 (SD 1.5) and the four-step Hyper-SD adapter for img2img.
- `identity`: the slower original mode, using SDXL, PhotoMaker v1, and `assets/reference/me.jpg`.
- `openrouter`: OpenRouter's image editing API. Set `OPENROUTER_API_KEY` in App Lab Brick Configuration, never in source or the Web UI.
- `preview`: deterministic local composite for testing capture, hardware, and UI without model weights.

Copy `config.example.json` to `config.json` to override defaults. `config.json` is optional. Settings include the NPU and cloud prompts, OpenRouter model, camera brightness/contrast, print enable, raster threshold, paper feed, heat dots/time/interval, density, and break time. The API key remains an App Lab Brick Configuration secret.

The web interface exposes all three test captures, the NPU instruction and optional `object.jpg` reference, the cloud composition prompt and model, camera processing, printer controls, a diagnostic print, and reprints from recent history. Applied values take effect on the next exposure and remain active until the App restarts. Source resolution controls preprocessing detail; the compiled editor graph always executes at 512x512.

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