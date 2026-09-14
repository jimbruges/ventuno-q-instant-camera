#!/usr/bin/env bash
set -euo pipefail

repo="${MODEL_REPO:-jimbruges/ventuno-q-instant-camera-models}"
revision="${MODEL_REVISION:-v1}"
target="${INSTANT_CAMERA_AI_HOME:-$HOME/instant-camera-ai}"
assets=(
  runtime-qnn-aarch64.tar.zst
  model-sd15-qcs8275.tar.zst
  model-instruct-pix2pix-qcs8275.tar.zst
  model-ip-adapter-reference-qcs8275.tar.zst
  model-ip-adapter-unet-qcs8275.tar.zst
  tokenizer-sd15.tar.zst
  model-licenses.tar.zst
)

temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT
mkdir -p "$target"

for command in awk curl python3 sha256sum tar zstd; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command is missing: $command" >&2
    exit 1
  fi
done
download_asset() {
  local asset="$1"
  local destination="$temporary_dir/$asset"
  curl --fail --location --retry 3 \
    "https://huggingface.co/$repo/resolve/$revision/$asset?download=true" \
    --output "$destination"
}

echo "Downloading model manifest from $repo revision $revision..."
download_asset SHA256SUMS

for asset in "${assets[@]}"; do
  expected="$(awk -v name="$asset" '$2 == name { print $1 }' "$temporary_dir/SHA256SUMS")"
  if [[ -z "$expected" ]]; then
    echo "Missing checksum for $asset" >&2
    exit 1
  fi
  echo "Downloading $asset..."
  download_asset "$asset"
  actual="$(sha256sum "$temporary_dir/$asset" | awk '{ print $1 }')"
  if [[ "$actual" != "$expected" ]]; then
    echo "Checksum mismatch for $asset" >&2
    exit 1
  fi
  tar --zstd -xf "$temporary_dir/$asset" -C "$target"
  rm "$temporary_dir/$asset"
done

required=(
  qnn-runtime/onnxruntime/__init__.py
  qnn-runtime/onnxruntime_qnn/__init__.py
  models/tokenizer/tokenizer_config.json
  models/qcs8275-sd15/stable_diffusion_v1_5-precompiled_qnn_onnx-w8a16-qualcomm_qcs8275/metadata.json
  models/qcs8275-instruct-pix2pix/vae_encoder/model.bin
  models/qcs8275-ip-adapter-plus/reference_encoder/model.bin
  models/qcs8275-ip-adapter-plus/adapter_unet/model.bin
  model-licenses/CREATIVEML-OPENRAIL-M.txt
)
for path in "${required[@]}"; do
  if [[ ! -f "$target/$path" ]]; then
    echo "Model bundle is incomplete: $target/$path is missing" >&2
    exit 1
  fi
done

PYTHONPATH="$target/qnn-runtime" LD_LIBRARY_PATH="/vendor/lib64" \
  python3 -c 'import numpy, onnxruntime, onnxruntime_qnn, PIL, transformers'

echo "Local NPU bundle installed in $target"
