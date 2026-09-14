---
license: creativeml-openrail-m
library_name: onnxruntime
pipeline_tag: image-to-image
tags:
  - arduino
  - ventuno-q
  - qnn
  - qualcomm
  - stable-diffusion
  - instruct-pix2pix
  - ip-adapter
---

# VENTUNO Q Instant Camera Models

Compiled local image-editing runtime for the [VENTUNO Q Instant Camera](https://github.com/jimbruges/ventuno-q-instant-camera).

This repository contains checksummed ARM64 Python runtime archives and Qualcomm QNN context binaries for the Arduino VENTUNO Q. The contexts are compiled for the target Qualcomm QCS8275 / HTP v75 profile and are not portable to unrelated hardware.

## Install

Use the installer from the source repository:

```bash
./scripts/install-models.sh
```

The installer pins the immutable `v1` tag, downloads one archive at a time, verifies `SHA256SUMS`, and extracts into `~/instant-camera-ai`.

## Contents

- Stable Diffusion 1.5 text encoder and VAE QNN contexts.
- InstructPix2Pix VAE encoder context.
- IP-Adapter Plus reference encoder and editor U-Net contexts.
- Stable Diffusion 1.5 CLIP tokenizer.
- ARM64 ONNX Runtime QNN Python environment.
- Upstream license texts and build provenance.

The complete experimental model tree is intentionally excluded. This repository contains only the components required by the supported `standard` local editor.

## Licensing

The compiled contexts are modified forms of upstream model components:

- Stable Diffusion 1.5: CreativeML OpenRAIL-M.
- InstructPix2Pix: MIT, with Stable Diffusion restrictions applying to derived model weights.
- IP-Adapter: Apache-2.0.
- ONNX Runtime QNN and its bundled Python dependencies retain their included licenses.

The model-license archive is installed under `~/instant-camera-ai/model-licenses`. Use of these artifacts is subject to CreativeML OpenRAIL-M, including its use restrictions. This repository is not affiliated with or endorsed by Arduino, Qualcomm, Stability AI, RunwayML, Timothy Brooks, or Tencent AI Lab.

## Provenance

The QNN contexts were compiled through Qualcomm AI Hub for the `Arduino VENTUNO Q` target. See `PROVENANCE.txt` in the license archive for component details. Checksums cover the exact downloadable archives; immutable repository tags identify published bundle versions.
