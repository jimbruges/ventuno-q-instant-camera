# VENTUNO Q AI Camera

An App Lab instant camera and thermal-printing photobooth for VENTUNO Q. It can run headless from Modulino Buttons while the Web UI provides configuration, test captures, test prints, and recent-print history.

## Headless controls

- Buttons A, B, and C each have independently configurable short-press and long-press profiles.
- Every profile can run Normal, Local, Cloud, or Describe mode. Describe sends the captured webcam scene to the built-in local VLM and prints its text description instead of an image.
- A press held for at least 900 ms selects the long-press profile; a shorter press selects the short-press profile.
- Press any Modulino button while capture, generation, printing, or Wi-Fi setup is active to cancel the process and return to Ready. The Web UI shutter performs the same action while a process is active.

Button A long press uses Describe by default. It runs locally through the `arduino:vlm` Brick and its configured `genie:qwen3_vl_4b_instruct` model; it does not require a cloud API key.

A button LED is steady when either of its profiles is available. Cloud profiles require their own reference image, an OpenRouter API key, and internet access. Local profiles require their selected NPU model to be ready. The selected button flashes while a job runs. The LED matrix shows ready, capture, processing/printing, success, and error states; Modulino Pixels provide the shutter flash.

The Web UI is optional during operation. Select one of the six profile tiles, configure it, and apply its settings; profiles and reference images persist across app and board restarts. With `camera` set to `auto`, the app selects the USB webcam's stable index-0 capture interface and ignores Qualcomm camera control/codec nodes whose `/dev/video*` numbers may change between boots.

## Screenless Wi-Fi setup

Hold Modulino Buttons A and C together for 1.5 seconds while the camera is ready. The LED matrix changes to a scanning frame and the Modulino Pixels illuminate the QR code. Show the webcam an Android Wi-Fi sharing QR code (`WIFI:...`) within two minutes. Press any button again to cancel scanning. The matrix shows connection progress, a checkmark on success, or an X on failure, and returns to the normal ready display automatically.

Wi-Fi setup supports open, WEP, WPA/WPA2, and WPA3/SAE sharing codes, including hidden networks. Network credentials are sent over an owner-only Unix socket to a host user service and are not written to the application configuration. After a successful scan, the thermal printer prints the decoded SSID and PSK, followed by a second ticket containing the connection result or failure message. The PSK is not written to application logs. The helper is installed on this board as `instant-camera-wifi.service`. To install it after copying the App to another board:

```bash
mkdir -p ~/.config/systemd/user
cp ~/ArduinoApps/instant-me-camera/tools/instant-camera-wifi.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now instant-camera-wifi.service
```

The scan timeout defaults to 120 seconds and can be overridden with `WIFI_SCAN_TIMEOUT` in App configuration.

## Startup

`VENTUNO Q AI Camera` is configured as the App CLI default and starts automatically when the board boots. The local Standard NPU model is managed by the enabled `instant-camera-npu.service` user service in `~/.config/systemd/user/`; user lingering keeps that service active without an interactive login. Local mode becomes available after model initialization, while Normal mode requires only the USB webcam. Describe mode uses the VLM configured for the App in App Lab. Cloud mode requires the webcam, reference portrait, network, and `OPENROUTER_API_KEY`.

## Thermal printer wiring

The printer is driven at 9600 baud (8-N-1) through `Serial1`:

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
- `identity`: the slower original mode, using SDXL, PhotoMaker v1, and `assets/reference/me.jpg`.
- `openrouter`: OpenRouter's image editing API. Set its API key in Cloud settings in the Web UI, or provide `OPENROUTER_API_KEY` through App Lab Brick Configuration.
- `preview`: deterministic local composite for testing capture, hardware, and UI without model weights.

Copy `config.example.json` to `config.json` to override defaults. `config.json` is optional. Settings include the NPU and cloud prompts, OpenRouter model, camera brightness/contrast, print enable, raster threshold, paper feed, heat dots/time/interval, density, and break time. A key entered in the Web UI is stored separately in ignored `.secrets.json` with owner-only permissions and is never returned to the browser; a blank key field preserves it.

The web interface exposes all four capture modes, the NPU instruction and optional `object.jpg` reference, the cloud composition prompt and model, camera processing, printer controls, a diagnostic print, and reprints from recent history. Applied values take effect on the next exposure and are persisted to `config.json` for future app and board restarts. Source resolution controls preprocessing detail; the compiled editor graph always executes at 512x512.

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
- `~/instant-camera-ai/models/sdxl-lightning.safetensors`: DreamShaper XL Lightning.
- `~/instant-camera-ai/models/photomaker-v1.safetensors`: PhotoMaker v1 identity adapter.
- `tools/ffmpeg`: bundled static ARM64 webcam capture binary.

The NPU editor uses 20 Euler ancestral steps with separate text and image guidance. A verified 512x512 edit completed in 25.2 seconds. Warm component timings were 310 ms for source encoding and about 400 ms for each editor U-Net pass; each step performs three U-Net passes. The identity profile took about six minutes with an 8.1 GiB peak.

GenieX is not used because its public runtime supports LLM and VLM inference rather than Stable Diffusion graphs. This backend uses Qualcomm's dedicated QNN Stable Diffusion export and `/dev/fastrpc-cdsp` directly through ONNX Runtime QNN.

Preprocessing preserves the webcam frame's native aspect ratio without padding, borders, or destructive center crops. Captures are not rotated by default; set `camera_rotation` to `90`, `-90`, or `180` only if the camera is mounted differently.

The Vulkan build detects the Adreno623, but Mesa Turnip rejects a generated compute shader because its workgroup barrier cannot schedule enough concurrent waves. The verified QNN backend avoids that Vulkan path.

The camera roll includes deterministic preview fixtures and outputs produced by the installed local models. OpenRouter support accepts either `message.images` or image items in the response content array.