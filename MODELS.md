# Local model installation

## Why models are release assets

The development board contains roughly 23 GB of model experiments. Committing those files to Git would make every clone enormous and exceeds GitHub's 100 MB file limit. Git LFS would still transfer all selected objects and consume LFS storage/bandwidth.

The supported installation pins a minimal VENTUNO Q bundle to the immutable `v1`
tag of the public Hugging Face model repository
`jimbruges/ventuno-q-instant-camera-models`. `scripts/install-models.sh`
downloads one asset at a time, verifies it against `SHA256SUMS`, extracts it,
and deletes the archive before continuing.

The release also installs the applicable upstream licenses under
`~/instant-camera-ai/model-licenses`. Installing or using the bundle means you
must comply with those terms, including the Stable Diffusion use restrictions.

The installer expects these assets plus `SHA256SUMS`:

```text
runtime-qnn-aarch64.tar.zst
model-sd15-qcs8275.tar.zst
model-instruct-pix2pix-qcs8275.tar.zst
model-ip-adapter-reference-qcs8275.tar.zst
model-ip-adapter-unet-qcs8275.tar.zst
tokenizer-sd15.tar.zst
model-licenses.tar.zst
```

## Default NPU bundle

The default Local mode requires:

- An ARM64 Python runtime with ONNX Runtime and the QNN execution provider.
- Qualcomm QCS8275 Stable Diffusion 1.5 text encoder and VAE contexts.
- FP16 InstructPix2Pix VAE encoder context.
- FP16 IP-Adapter Plus reference encoder and editor U-Net contexts.
- A pinned Stable Diffusion 1.5 CLIP tokenizer for offline startup.

The NPU service runs with `HF_HUB_OFFLINE=1`; after installation it does not
download model or tokenizer files at startup.

Installed layout:

```text
~/instant-camera-ai/
├── bin/start-npu-router
├── src/
├── qnn-runtime/
└── models/
    ├── tokenizer/
    ├── qcs8275-sd15/
    ├── qcs8275-instruct-pix2pix/
    └── qcs8275-ip-adapter-plus/
```

The board firmware identifies the product as `arduino,monza`. Qualcomm tooling names
the compatible compiled-context target QCS8275/HTP v75; those directory names are
expected and do not indicate a board-detection mismatch. The contexts are not portable
to unrelated boards.

## Upstream models

The compiled pipeline derives from:

- Stable Diffusion 1.5: `stable-diffusion-v1-5/stable-diffusion-v1-5`, CreativeML OpenRAIL-M.
- InstructPix2Pix: `timbrooks/instruct-pix2pix`, MIT.
- IP-Adapter: `h94/IP-Adapter`, Apache-2.0.

Users remain responsible for complying with the upstream licenses and acceptable-use terms. The release contains compiled inference contexts, not the full source checkpoints.

## Install or verify

```bash
./scripts/install-models.sh
systemctl --user restart instant-camera-npu.service
curl http://127.0.0.1:7100/health
```

Override the repository or revision when testing a new bundle:

```bash
MODEL_REVISION=v2 ./scripts/install-models.sh
MODEL_REPO=another-user/another-model ./scripts/install-models.sh
```

`INSTANT_CAMERA_AI_HOME` overrides the default `~/instant-camera-ai` target for
manual model installs and launcher testing. The systemd unit intentionally uses
the canonical home-relative location installed by `install.sh`.

No Hugging Face account or CLI is required to install the public bundle; downloads
use HTTPS and `curl`.

## Rebuilding

The export sources are in `instant-camera-ai/src/`. Rebuilding requires a Linux development machine with PyTorch, Diffusers, ONNX, a Hugging Face connection, and a Qualcomm AI Hub account/API token. The main entry points are:

```bash
python instant-camera-ai/src/export_instruct_pix2pix.py all
python instant-camera-ai/src/export_ip_adapter_pix2pix.py prepare
```

Compilation jobs target the `Arduino VENTUNO Q` device through Qualcomm AI Hub. Downloaded job outputs must be arranged in the installed layout above. Export environments and source checkpoints are intentionally excluded from Git because they are large build inputs, not board runtime dependencies.

Optional CPU, identity, ControlNet, and batched experiments are not part of the default bundle. They are not required for Normal, Cloud, or the supported Standard NPU mode.

Maintainers can assemble a replacement release directly from a prepared VENTUNO Q
without retaining duplicate archives:

```bash
MODEL_REVISION=v2 ./scripts/publish-hf-models.sh --publish
```

On a board with limited internal storage, point the one-at-a-time archive
workspace at a mounted USB drive:

```bash
MODEL_ARCHIVE_TMPDIR=/media/arduino/UNTITLED \
    MODEL_REVISION=v2 ./scripts/publish-hf-models.sh --publish
```

The publisher validates all inputs, embeds the upstream model licenses, uploads
one archive at a time, and creates the immutable revision tag only after all
checksums have been uploaded.
