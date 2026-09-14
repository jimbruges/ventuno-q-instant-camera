#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--publish" ]]; then
  echo "Usage: $0 --publish" >&2
  echo "Creates and publishes the model bundle from installed board artifacts." >&2
  exit 2
fi

repo="${MODEL_REPO:-jimbruges/ventuno-q-instant-camera-models}"
revision="${MODEL_REVISION:-v1}"
ai_root="${INSTANT_CAMERA_AI_HOME:-$HOME/instant-camera-ai}"
hf_cache="${HF_HOME:-$HOME/.cache/huggingface}/hub"
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tokenizer_revision="451f4fe16113bff5a5d2269ed5ad43b0592e9a14"
archive_tmp_root="${MODEL_ARCHIVE_TMPDIR:-${TMPDIR:-/tmp}}"
mkdir -p "$archive_tmp_root"
temporary_dir="$(mktemp -d "$archive_tmp_root/instant-camera-models.XXXXXX")"
manifest="$temporary_dir/SHA256SUMS"
upload_manifest="$(mktemp)"
trap 'rm -rf "$temporary_dir"; rm -f "$upload_manifest"' EXIT

for command in curl hf sha256sum tar zstd; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command is missing: $command" >&2
    exit 1
  fi
done

hf auth whoami >/dev/null
if [[ ! -w "$archive_tmp_root" ]]; then
  echo "Archive workspace is not writable: $archive_tmp_root" >&2
  exit 1
fi
if hf repos tag list "$repo" 2>/dev/null | grep -Eq "(^|[[:space:]])$revision([[:space:]]|$)"; then
  echo "Tag $revision already exists in $repo; choose a new MODEL_REVISION." >&2
  exit 1
fi

required_paths=(
  "$ai_root/qnn-runtime/onnxruntime/__init__.py"
  "$ai_root/qnn-runtime/onnxruntime_qnn/__init__.py"
  "$ai_root/models/qcs8275-sd15/stable_diffusion_v1_5-precompiled_qnn_onnx-w8a16-qualcomm_qcs8275/metadata.json"
  "$ai_root/models/qcs8275-instruct-pix2pix/vae_encoder/model.bin"
  "$ai_root/models/qcs8275-ip-adapter-plus/reference_encoder/model.bin"
  "$ai_root/models/qcs8275-ip-adapter-plus/adapter_unet/model.bin"
)
for path in "${required_paths[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Required publishing input is missing: $path" >&2
    exit 1
  fi
done

tokenizer_source="$hf_cache/models--stable-diffusion-v1-5--stable-diffusion-v1-5/snapshots/$tokenizer_revision/tokenizer"
if [[ ! -d "$tokenizer_source" ]]; then
  echo "The Stable Diffusion 1.5 tokenizer is not present in $hf_cache." >&2
  exit 1
fi

mkdir -p "$temporary_dir/tokenizer-root/models/tokenizer" "$temporary_dir/licenses-root/model-licenses"
cp -LR "$tokenizer_source/." "$temporary_dir/tokenizer-root/models/tokenizer/"
curl --fail --location --retry 3 \
  https://raw.githubusercontent.com/CompVis/stable-diffusion/main/LICENSE \
  --output "$temporary_dir/licenses-root/model-licenses/CREATIVEML-OPENRAIL-M.txt"
curl --fail --location --retry 3 \
  https://raw.githubusercontent.com/timothybrooks/instruct-pix2pix/main/LICENSE \
  --output "$temporary_dir/licenses-root/model-licenses/INSTRUCT-PIX2PIX.txt"
curl --fail --location --retry 3 \
  https://raw.githubusercontent.com/tencent-ailab/IP-Adapter/main/LICENSE \
  --output "$temporary_dir/licenses-root/model-licenses/IP-ADAPTER.txt"
cp "$source_root/huggingface/PROVENANCE.txt" "$temporary_dir/licenses-root/model-licenses/"

hf repos create "$repo" --public --exist-ok
hf upload "$repo" "$source_root/huggingface/README.md" README.md

upload_archive() {
  local name="$1"
  local base="$2"
  shift 2
  local archive="$temporary_dir/$name"
  echo "Packaging $name..."
  tar --zstd -cf "$archive" -C "$base" "$@"
  sha256sum "$archive" | sed "s|$temporary_dir/||" >>"$manifest"
  hf upload "$repo" "$archive" "$name"
  rm "$archive"
}

upload_archive runtime-qnn-aarch64.tar.zst "$ai_root" qnn-runtime
upload_archive model-sd15-qcs8275.tar.zst "$ai_root" models/qcs8275-sd15
upload_archive model-instruct-pix2pix-qcs8275.tar.zst "$ai_root" models/qcs8275-instruct-pix2pix/vae_encoder
upload_archive model-ip-adapter-reference-qcs8275.tar.zst "$ai_root" models/qcs8275-ip-adapter-plus/reference_encoder
upload_archive model-ip-adapter-unet-qcs8275.tar.zst "$ai_root" models/qcs8275-ip-adapter-plus/adapter_unet
upload_archive tokenizer-sd15.tar.zst "$temporary_dir/tokenizer-root" models/tokenizer
upload_archive model-licenses.tar.zst "$temporary_dir/licenses-root" model-licenses

cp "$manifest" "$upload_manifest"
hf upload "$repo" "$upload_manifest" SHA256SUMS
hf repos tag create "$repo" "$revision" -m "VENTUNO Q model bundle $revision"
echo "Published https://huggingface.co/$repo at immutable tag $revision"