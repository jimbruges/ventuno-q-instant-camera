#!/usr/bin/env bash
set -euo pipefail

source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_source="$source_root/ArduinoApps/instant-me-camera"
ai_source="$source_root/instant-camera-ai"
app_target="$HOME/ArduinoApps/instant-me-camera"
ai_target="$HOME/instant-camera-ai"
install_models=true
start_services=true

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --skip-models  Install the app without downloading the local NPU bundle.
  --no-start     Do not start services, set the default app, or start the app.
  -h, --help     Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-models) install_models=false ;;
    --no-start) start_services=false ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

compatible="$(tr '\0' '\n' </sys/firmware/devicetree/base/compatible 2>/dev/null | head -n 1 || true)"
if [[ "$compatible" != "arduino,monza" ]]; then
  echo "This installer requires an Arduino VENTUNO Q (arduino,monza); found ${compatible:-unknown}." >&2
  exit 1
fi

for command in arduino-app-cli curl rsync systemctl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command is missing: $command" >&2
    exit 1
  fi
done

sync_tree() {
  local source="$1"
  local destination="$2"
  shift 2
  mkdir -p "$destination"
  if [[ "$(realpath "$source")" != "$(realpath "$destination")" ]]; then
    rsync -a "$@" "$source/" "$destination/"
  fi
}

echo "Installing Arduino App in $app_target..."
sync_tree "$app_source" "$app_target" \
  --exclude .cache/ \
  --exclude .secrets.json \
  --exclude .wifi-provision.sock \
  --exclude .wifi-qr-frame.jpg \
  --exclude config.json \
  --exclude assets/captures/ \
  --exclude assets/reference/profiles/

echo "Installing NPU source in $ai_target..."
mkdir -p "$ai_target/src" "$ai_target/bin"
sync_tree "$ai_source/src" "$ai_target/src"
if [[ "$(realpath "$ai_source/bin/start-npu-router")" != "$(realpath -m "$ai_target/bin/start-npu-router")" ]]; then
  install -m 0755 "$ai_source/bin/start-npu-router" "$ai_target/bin/start-npu-router"
fi

if $install_models; then
  if [[ ! -f "$ai_target/models/qcs8275-ip-adapter-plus/adapter_unet/model.bin" ]]; then
    available_kib="$(df -Pk "$HOME" | awk 'NR == 2 { print $4 }')"
    required_kib=$((8 * 1024 * 1024))
    if (( available_kib < required_kib )); then
      echo "A fresh local model install requires at least 8 GiB free; only $((available_kib / 1024 / 1024)) GiB is available." >&2
      exit 1
    fi
  fi
  INSTANT_CAMERA_AI_HOME="$ai_target" "$source_root/scripts/install-models.sh"
fi

user_units="$HOME/.config/systemd/user"
mkdir -p "$user_units"
install -m 0644 "$ai_source/systemd/instant-camera-npu.service" \
  "$user_units/instant-camera-npu.service"
install -m 0644 "$app_target/tools/instant-camera-wifi.service" \
  "$user_units/instant-camera-wifi.service"
systemctl --user daemon-reload

if ! $start_services; then
  echo "Files installed. Services and Arduino App were not started."
  exit 0
fi

systemctl --user enable --now instant-camera-wifi.service
if [[ -f "$ai_target/models/qcs8275-ip-adapter-plus/adapter_unet/model.bin" ]]; then
  systemctl --user enable --now instant-camera-npu.service
  echo "Waiting for the local NPU endpoint..."
  curl --fail --silent --show-error --retry 60 --retry-delay 2 --retry-connrefused \
    http://127.0.0.1:7100/health
  echo
else
  echo "Local models are absent; leaving the NPU service disabled."
fi

arduino-app-cli properties set default "$app_target"
arduino-app-cli app start "$app_target"

echo "Instant camera installed. Open http://<board-ip>:7000"
