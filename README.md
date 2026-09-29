# VENTUNO Q Instant Camera

![VENTUNO Q Rubber Duck Camera](docs/images/header.png)

A headless instant-camera photobooth for Arduino VENTUNO Q. It combines a USB webcam, Modulino Buttons and Pixels, the onboard LED matrix, a serial thermal printer, and optional local NPU or cloud image editing.

## What it does

- Six configurable short/long button profiles across Normal, Local NPU, and Cloud modes.
- Captures, edits, archives, and prints 384-dot monochrome receipts.
- Provides a browser UI on port 7000 for configuration, previews, test prints, and history.
- Provisions Wi-Fi from an Android sharing QR code without a display.
- Runs the local editor through QNN/HTP contexts compiled for the VENTUNO Q QCS8275.

## Hardware

- Arduino VENTUNO Q with current App Lab image and `arduino-app-cli`.
- UVC USB webcam.
- Modulino Buttons and Modulino Pixels connected over Qwiic/I2C.
- TTL thermal printer on D1/TX at 9600 baud, powered from a separate suitable supply with common ground.

See [the app README](ArduinoApps/instant-me-camera/README.md) for controls and detailed printer wiring.

## 3D-printed enclosure

The camera ships in a three-part enclosure sized around the VENTUNO Q, webcam, thermal printer, and Modulino buttons: a top shell with the carry handle and button/lens cutouts, a bottom shell with a printer bay, and a vented side panel for airflow around the board.

| Front cutouts | Vented side panel | Interior shell |
| --- | --- | --- |
| ![Front panel with lens, button, and printer cutouts](docs/images/case-cad-front.png) | ![Side panel with ventilation slots and carry handle](docs/images/case-cad-side-vents.png) | ![Interior view of the printed shell](docs/images/case-cad-isometric.png) |

All three parts print without supports; the print layout below splits them across two plates:

![Slicer plate layout for the lid, body, and side panel](docs/images/case-print-plates.png)

The editable CAD source is in [hardware/ventuno-q-instant-camera.3mf](hardware/ventuno-q-instant-camera.3mf) (Fusion 360 / PrusaSlicer compatible).

## Install on a VENTUNO Q

The default installation downloads about 3.1 GB of compressed NPU runtime/model
artifacts from Hugging Face, installs about 5 GB, and needs at least 8 GB free
during installation.
The source repository is currently private, so authenticate GitHub CLI before
cloning it. Model downloads are public and require no account.

```bash
gh auth login
gh repo clone jimbruges/ventuno-q-instant-camera
cd ventuno-q-instant-camera
./install.sh
```

The installer:

1. Verifies that it is running on a VENTUNO Q.
2. Copies the Arduino App to `~/ArduinoApps/instant-me-camera`.
3. Downloads and checksum-verifies the pinned Hugging Face `v1` model bundle.
4. Installs the portable NPU and Wi-Fi user services.
5. Enables both services, sets the app as the boot default, and starts it.

Then open `http://<board-ip>:7000`. The first NPU startup may take up to two minutes.

Useful alternatives:

```bash
./install.sh --skip-models   # Normal and Cloud modes only
./install.sh --no-start      # Install without starting or changing the default app
./scripts/install-models.sh  # Install/reinstall only the local NPU bundle
```

The installer preserves runtime `config.json`, `.secrets.json`, captures, and profile photos on upgrades.

## Configuration

The app works without `config.json`; defaults are portable and discover the Docker host gateway automatically. To customize settings manually:

```bash
cp ~/ArduinoApps/instant-me-camera/config.example.json \
   ~/ArduinoApps/instant-me-camera/config.json
```

Cloud mode needs an OpenRouter API key entered in the Web UI. It is stored in the ignored, owner-only `.secrets.json` file and is never committed.

## Models and large files

Model files are not stored in Git history. The complete experimental model
directory on the development board is about 23 GB, while the supported bundle is
about 5 GB. It is distributed as checksummed artifacts from a dedicated Hugging
Face model repository. This keeps source clones small while pinning the exact
ARM64 runtime and QCS8275 contexts.

See [MODELS.md](MODELS.md) for artifact contents, upstream model licenses, optional backends, and rebuilding details.

## Repository layout

- `ArduinoApps/instant-me-camera/`: App Lab application, MCU sketch, Web UI, and Wi-Fi helper.
- `instant-camera-ai/src/`: local QNN pipeline and model export source.
- `instant-camera-ai/bin/start-npu-router`: portable NPU service launcher.
- `instant-camera-ai/systemd/`: user service template.
- `scripts/install-models.sh`: release artifact downloader and verifier.
- `install.sh`: complete board installer.
- `hardware/`: 3D-printable enclosure source (`.3mf`).
- `docs/images/`: README screenshots and renders.

## Operations

```bash
systemctl --user status instant-camera-npu.service
systemctl --user status instant-camera-wifi.service
curl http://127.0.0.1:7100/health
arduino-app-cli app logs ~/ArduinoApps/instant-me-camera --tail 100
arduino-app-cli app start ~/ArduinoApps/instant-me-camera
```

Only one Arduino App can run at a time. Starting this app stops the currently running app.
