import base64
import copy
import io
import json
import math
import os
import queue
import socket
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat
from arduino.app_bricks.vlm import VisionLanguageModel
from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge, Logger
from model_service import request_model_service
from receipt_effects import receipt_raster, send_to_printer
from wifi_setup import connect_wifi, scan_wifi_qr


APP_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_DIR / "assets"
CAPTURES_DIR = ASSETS_DIR / "captures"
NPU_REFERENCE_PATH = ASSETS_DIR / "reference" / "object.jpg"
PROFILE_REFERENCE_DIR = ASSETS_DIR / "reference" / "profiles"
CONFIG_PATH = APP_DIR / "config.json"
SECRETS_PATH = APP_DIR / ".secrets.json"
DEFAULT_PROMPT = "same room, candid realistic instant camera photograph, one large yellow rubber duck centered on the floor in the masked area"
DEFAULT_NPU_PROMPT = "Please replace the just the head of any human in this image with a large rubber duck. Preserve every person, their clothes, the room, lighting, and camera angle."
DEFAULT_CLOUD_PROMPT = "Place the person from the second reference image naturally into the scene in the first image. Keep their face, body, clothes, and identity recognizable. If people are present, pose them together; otherwise place the person naturally in the background. Preserve the scene, lighting, camera angle, and realistic photographic style."
VLM_PROMPT = "/no_think Describe this image in two concise sentences for a printed receipt. Mention the main people, objects, setting, and action. Do not use markdown."
OUTPUT_WIDTH = 200
MODE_NAMES = {"normal": 0, "cloud": 1, "local": 2, "describe": 3}
PROFILE_IDS = (
    "a_short", "b_short", "c_short",
    "a_long", "b_long", "c_long",
)
DEFAULT_PROFILE_MODES = {
    "a_short": "normal", "b_short": "cloud", "c_short": "local",
    "a_long": "describe", "b_long": "cloud", "c_long": "local",
}
NPU_MODEL_DEFAULTS = {
    "standard": {
        "prompt": DEFAULT_NPU_PROMPT,
        "resolution": 512,
        "steps": 20,
        "guidance_scale": 7.5,
        "image_guidance_scale": 1.5,
        "seed": None,
    }
}

logger = Logger("InstantMeCamera")
ui = WebUI()
vlm = VisionLanguageModel(
    system_prompt="You are a concise visual narrator. Report only details visible in the image.",
    temperature=0.3,
    max_tokens=256,
    timeout=600,
)
jobs = queue.Queue(maxsize=1)
state_lock = threading.Lock()
printer_lock = threading.Lock()
wifi_cancel_event = threading.Event()
process_cancel_event = threading.Event()
state = {
    "status": "ready",
    "message": "Ready",
    "backend": "preview",
    "camera": "/dev/video0",
    "busy": False,
    "active_mode": None,
    "active_gesture": None,
    "model_service": {"status": "unknown", "action": None},
    "availability": {profile_id: False for profile_id in PROFILE_IDS},
    "photos": [],
}


def host_gateway():
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1] == "00000000":
                address = bytes.fromhex(fields[2])
                return ".".join(str(part) for part in reversed(address))
    except (IndexError, OSError, ValueError):
        pass
    return "172.17.0.1"


def load_config():
    config = {
        "backend": os.getenv("CAMERA_BACKEND", "npu"),
        "camera": os.getenv("CAMERA_DEVICE", "/dev/video0"),
        "prompt": os.getenv("CAMERA_PROMPT", DEFAULT_PROMPT),
        "npu_prompt": os.getenv("NPU_PROMPT", DEFAULT_NPU_PROMPT),
        "npu_url": os.getenv("NPU_URL", f"http://{host_gateway()}:7100/edit"),
        "npu_steps": int(os.getenv("NPU_STEPS", "20")),
        "npu_guidance_scale": float(os.getenv("NPU_GUIDANCE_SCALE", "7.5")),
        "npu_image_guidance_scale": float(os.getenv("NPU_IMAGE_GUIDANCE_SCALE", "1.5")),
        "npu_seed": None,
        "npu_model": "standard",
        "cloud_prompt": os.getenv("CLOUD_PROMPT", DEFAULT_CLOUD_PROMPT),
        "generation_width": int(os.getenv("GENERATION_WIDTH", "384")),
        "generation_steps": int(os.getenv("GENERATION_STEPS", "4")),
        "generation_strength": float(os.getenv("GENERATION_STRENGTH", "0.78")),
        "camera_rotation": int(os.getenv("CAMERA_ROTATION", "0")),
        "openrouter_model": os.getenv("OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image"),
        "camera_brightness": float(os.getenv("CAMERA_BRIGHTNESS", "0")),
        "camera_contrast": float(os.getenv("CAMERA_CONTRAST", "1")),
        "printer_enabled": os.getenv("PRINTER_ENABLED", "true").lower() not in {"0", "false", "no"},
        "printer_threshold": int(os.getenv("PRINTER_THRESHOLD", "96")),
        "printer_dither": os.getenv("PRINTER_DITHER", "true").lower() not in {"0", "false", "no"},
        "printer_feed_lines": int(os.getenv("PRINTER_FEED_LINES", "3")),
        "printer_heat_dots": int(os.getenv("PRINTER_HEAT_DOTS", "5")),
        "printer_heat_time": int(os.getenv("PRINTER_HEAT_TIME", "162")),
        "printer_heat_interval": int(os.getenv("PRINTER_HEAT_INTERVAL", "40")),
        "printer_density": int(os.getenv("PRINTER_DENSITY", "4")),
        "printer_break_time": int(os.getenv("PRINTER_BREAK_TIME", "2")),
        "print_height_scale": float(os.getenv("PRINT_HEIGHT_SCALE", "0.92")),
        "wifi_scan_timeout": int(os.getenv("WIFI_SCAN_TIMEOUT", "120")),
    }
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    profiles = {
        name: dict(defaults) for name, defaults in NPU_MODEL_DEFAULTS.items()
    }
    saved_profiles = config.get("npu_profiles", {})
    if isinstance(saved_profiles, dict):
        for name, profile in saved_profiles.items():
            if name in profiles and isinstance(profile, dict):
                profiles[name].update(profile)
    if not saved_profiles:
        profiles["standard"].update({
            "prompt": config["npu_prompt"],
            "steps": config["npu_steps"],
            "guidance_scale": config["npu_guidance_scale"],
            "image_guidance_scale": config["npu_image_guidance_scale"],
            "seed": config["npu_seed"],
        })
    config["npu_profiles"] = profiles
    config["npu_model"] = "standard"
    default_local = profiles[config["npu_model"]]
    button_profiles = {}
    saved_button_profiles = config.get("button_profiles", {})
    for profile_id in PROFILE_IDS:
        profile = {
            "mode": DEFAULT_PROFILE_MODES[profile_id],
            "npu_model": config["npu_model"],
            "prompt": default_local["prompt"],
            "resolution": default_local["resolution"],
            "steps": default_local["steps"],
            "guidance_scale": default_local["guidance_scale"],
            "image_guidance_scale": default_local["image_guidance_scale"],
            "seed": default_local["seed"],
            "cloud_prompt": config["cloud_prompt"],
            "openrouter_model": config["openrouter_model"],
        }
        saved_profile = saved_button_profiles.get(profile_id, {})
        if isinstance(saved_profile, dict):
            profile.update(saved_profile)
        if profile["mode"] not in MODE_NAMES:
            profile["mode"] = DEFAULT_PROFILE_MODES[profile_id]
        profile["npu_model"] = "standard"
        button_profiles[profile_id] = profile
    config["button_profiles"] = button_profiles
    return config


config = load_config()
state["backend"] = config["backend"]
state["camera"] = config["camera"]


def profile_reference_path(profile_id):
    return PROFILE_REFERENCE_DIR / f"{profile_id}.jpg"


def migrate_profile_references():
    if PROFILE_REFERENCE_DIR.exists():
        return
    PROFILE_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    for profile_id, profile in config["button_profiles"].items():
        legacy_path = NPU_REFERENCE_PATH
        if profile["mode"] != "normal" and legacy_path.is_file():
            shutil.copyfile(legacy_path, profile_reference_path(profile_id))


migrate_profile_references()


def saved_openrouter_api_key():
    try:
        secrets = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        return str(secrets.get("openrouter_api_key", "")).strip()
    except (AttributeError, json.JSONDecodeError, OSError):
        return ""


def openrouter_api_key():
    return saved_openrouter_api_key() or os.getenv("OPENROUTER_API_KEY", "").strip()


def save_openrouter_api_key(api_key):
    if not api_key:
        SECRETS_PATH.unlink(missing_ok=True)
        return
    temporary_secrets = SECRETS_PATH.with_suffix(".json.tmp")
    temporary_secrets.write_text(
        json.dumps({"openrouter_api_key": api_key}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_secrets.chmod(0o600)
    temporary_secrets.replace(SECRETS_PATH)
    SECRETS_PATH.chmod(0o600)


def photo_records():
    records = []
    for path in sorted(CAPTURES_DIR.glob("*-final.png"), reverse=True):
        stem = path.name.removesuffix("-final.png")
        parts = stem.rsplit("-", 1)
        mode = parts[1] if len(parts) == 2 and parts[1] in MODE_NAMES else "legacy"
        records.append({
            "id": stem,
            "image": f"captures/{path.name}",
            "original": f"captures/{stem}-original.jpg",
            "print": f"captures/{stem}-print.png" if (CAPTURES_DIR / f"{stem}-print.png").exists() else None,
            "mode": mode,
            "created": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        })
    return records[:24]


def snapshot_state():
    with state_lock:
        result = dict(state)
        result["photos"] = photo_records()
        result["profile_references"] = {
            profile_id: (
                f"reference/profiles/{profile_id}.jpg?v={profile_reference_path(profile_id).stat().st_mtime_ns}"
                if profile_reference_path(profile_id).is_file()
                else None
            )
            for profile_id in PROFILE_IDS
        }
        result["settings"] = {
            "button_profiles": copy.deepcopy(config["button_profiles"]),
            "camera_brightness": config["camera_brightness"],
            "camera_contrast": config["camera_contrast"],
            "printer_enabled": config["printer_enabled"],
            "printer_threshold": config["printer_threshold"],
            "printer_dither": config["printer_dither"],
            "printer_feed_lines": config["printer_feed_lines"],
            "printer_heat_dots": config["printer_heat_dots"],
            "printer_heat_time": config["printer_heat_time"],
            "printer_heat_interval": config["printer_heat_interval"],
            "printer_density": config["printer_density"],
            "printer_break_time": config["printer_break_time"],
        }
        saved_key = saved_openrouter_api_key()
        result["openrouter_key_ready"] = bool(saved_key or os.getenv("OPENROUTER_API_KEY"))
        result["openrouter_key_source"] = "saved" if saved_key else "environment" if os.getenv("OPENROUTER_API_KEY") else None
        required = []
        result["local_ready"] = all(Path(path).is_file() for path in required)
        return result


def publish(status, message, busy=None):
    with state_lock:
        state["status"] = status
        state["message"] = message
        if busy is not None:
            state["busy"] = busy
    try:
        Bridge.call("camera_status", status)
    except Exception as exc:
        logger.info(f"Matrix status update skipped: {exc}")
    ui.send_message("camera_state", snapshot_state())


def check_cancelled():
    if process_cancel_event.is_set():
        raise InterruptedError("Process cancelled")


def run_interruptibly(operation):
    result_queue = queue.Queue(maxsize=1)

    def execute():
        try:
            result_queue.put((True, operation()))
        except BaseException as exc:
            result_queue.put((False, exc))

    threading.Thread(target=execute, daemon=True).start()
    while True:
        check_cancelled()
        try:
            succeeded, result = result_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if succeeded:
            return result
        raise result


def wait_interruptibly(seconds):
    if process_cancel_event.wait(seconds):
        check_cancelled()


def resolve_camera_device():
    configured = str(config["camera"])
    candidates = []
    if configured != "auto":
        candidates.append(Path(configured))
    candidates.extend(sorted(Path("/dev/v4l/by-id").glob("*-video-index0")))
    candidates.extend(
        Path("/dev") / device.name
        for device in sorted(Path("/sys/class/video4linux").glob("video*"))
        if "/usb" in str(device.resolve())
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        device = candidate.resolve()
        sysfs_device = Path("/sys/class/video4linux") / device.name
        if sysfs_device.exists() and "/usb" in str(sysfs_device.resolve()):
            return str(device)
    return None


def capture_frame(destination):
    camera_device = resolve_camera_device()
    if camera_device is None:
        raise RuntimeError("USB webcam is not connected")
    bundled_ffmpeg = APP_DIR / "tools" / "ffmpeg"
    ffmpeg_args = [
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "640x480",
        "-i", camera_device,
        "-ss", "0.8",
        "-vf", f"eq=brightness={config['camera_brightness']}:contrast={config['camera_contrast']}",
        "-frames:v", "1", str(destination),
    ]
    if bundled_ffmpeg.is_file():
        command = [str(bundled_ffmpeg), *ffmpeg_args]
    elif shutil.which("ffmpeg"):
        command = ["ffmpeg", *ffmpeg_args]
    elif shutil.which("docker"):
        container_output = f"/output/{destination.name}"
        command = [
            "docker", "run", "--rm", "--device", camera_device,
            "-v", f"{destination.parent}:/output", "alpine:3.21",
            "sh", "-c", "apk add --no-cache ffmpeg >/dev/null && exec ffmpeg \"$@\"",
            "ffmpeg", *ffmpeg_args[:-1], container_output,
        ]
    else:
        raise RuntimeError("Camera capture requires ffmpeg or Docker")
    process = None
    try:
        process = subprocess.Popen(command)
        deadline = time.monotonic() + 15
        while process.poll() is None:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, 15)
            time.sleep(0.05)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    except InterruptedError:
        if process is not None and process.poll() is None:
            process.terminate()
        raise
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if process is not None and process.poll() is None:
            process.kill()
        raise RuntimeError(f"Camera capture failed: {exc}") from exc


def enhance_scene(image):
    sample = image.copy()
    sample.thumbnail((160, 120), Image.Resampling.BILINEAR)
    channel_means = ImageStat.Stat(sample).mean
    neutral_mean = sum(channel_means) / len(channel_means)
    gains = [
        max(0.75, min(1.33, neutral_mean / max(channel_mean, 1.0)))
        for channel_mean in channel_means
    ]
    balanced = Image.merge(
        "RGB",
        tuple(
            ImageEnhance.Brightness(channel).enhance(gain)
            for channel, gain in zip(image.split(), gains)
        ),
    )
    luminance = ImageStat.Stat(sample.convert("L")).mean[0]
    if luminance >= 110:
        return balanced
    gamma = max(
        0.35,
        min(1.0, math.log(105 / 255) / math.log(max(luminance, 1.0) / 255)),
    )
    lookup = [round(255 * ((value / 255) ** gamma)) for value in range(256)]
    corrected = balanced.point(lookup * 3)
    return ImageEnhance.Contrast(corrected).enhance(1.08)


def prepare_scene(source, destination, profile):
    with Image.open(source) as image:
        prepared = enhance_scene(ImageOps.exif_transpose(image).convert("RGB"))
        if config["camera_rotation"]:
            prepared = prepared.rotate(config["camera_rotation"], expand=True)
        generation_width = (
            profile["resolution"]
            if profile["mode"] == "local"
            else config["generation_width"]
        )
        generation_height = round(generation_width * prepared.height / prepared.width)
        prepared.resize(
            (generation_width, generation_height), Image.Resampling.LANCZOS
        ).save(destination, quality=92)


def generate_npu(scene, output, profile, reference_path):
    model = profile["npu_model"]
    seed = profile["seed"]
    set_accelerator_mode("local", model)
    payload = {
        "model": model,
        "prompt": profile["prompt"],
        "image": base64.b64encode(scene.read_bytes()).decode("ascii"),
        "steps": profile["steps"],
        "guidance_scale": profile["guidance_scale"],
        "image_guidance_scale": profile["image_guidance_scale"],
        "seed": seed if seed is not None else time.time_ns() & 0x7FFFFFFF,
    }
    if reference_path.is_file():
        payload["reference_image"] = base64.b64encode(
            reference_path.read_bytes()
        ).decode("ascii")
    request = urllib.request.Request(
        config["npu_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        content_type, body = run_interruptibly(lambda: _read_url(request, 900))
        if content_type != "image/png":
            raise RuntimeError("NPU service returned an invalid response")
        check_cancelled()
        output.write_bytes(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"NPU service failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NPU service is unavailable: {exc.reason}") from exc


def _read_url(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.headers.get_content_type(), response.read()


def _read_json_url(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def encode_data_url(path):
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def encode_context_data_url(path, canvas_size):
    with Image.open(path) as image:
        context = ImageOps.exif_transpose(image).convert("RGB")
        context.thumbnail(canvas_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", canvas_size, "white")
    offset = (
        (canvas.width - context.width) // 2,
        (canvas.height - context.height) // 2,
    )
    canvas.paste(context, offset)
    encoded = io.BytesIO()
    canvas.save(encoded, format="JPEG", quality=88, optimize=True)
    return f"data:image/jpeg;base64,{base64.b64encode(encoded.getvalue()).decode('ascii')}"


def generate_openrouter(scene, output, profile, reference_path):
    api_key = openrouter_api_key()
    if not api_key:
        raise RuntimeError("OpenRouter API key is not configured")
    with Image.open(scene) as scene_image:
        scene_size = scene_image.size
        aspect_ratio = "4:3" if scene_image.width >= scene_image.height else "3:4"
    payload = {
        "model": profile["openrouter_model"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": profile["cloud_prompt"]},
            {"type": "image_url", "image_url": {"url": encode_data_url(scene)}},
            {"type": "image_url", "image_url": {"url": encode_context_data_url(reference_path, scene_size)}},
        ]}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": aspect_ratio},
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        result = run_interruptibly(lambda: _read_json_url(request, 180))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc}") from exc
    message = result["choices"][0]["message"]
    images = message.get("images", [])
    if not images and isinstance(message.get("content"), list):
        images = [item for item in message["content"] if isinstance(item, dict) and item.get("type") == "image_url"]
    if not images:
        raise RuntimeError("OpenRouter returned no image")
    data_url = images[0].get("image_url", {}).get("url") or images[0].get("url", "")
    if not data_url.startswith("data:") or "," not in data_url:
        raise RuntimeError("OpenRouter returned an unsupported image URL")
    try:
        output.write_bytes(base64.b64decode(data_url.split(",", 1)[1], validate=True))
    except ValueError as exc:
        raise RuntimeError("OpenRouter returned invalid image data") from exc


def generate_preview(scene, output):
    with Image.open(scene) as background:
        canvas = background.convert("RGB")
        draw = ImageDraw.Draw(canvas)
        height = canvas.height
        draw.ellipse((24, height - 82, 118, height - 15), fill=190, outline=30, width=3)
        draw.ellipse((72, height - 116, 126, height - 62), fill=205, outline=30, width=3)
        draw.polygon(((118, height - 98), (150, height - 86), (119, height - 78)), fill=155, outline=30)
        draw.ellipse((108, height - 101, 114, height - 95), fill=20)
        canvas.save(output)


def finish_image(generated, scene, output):
    with Image.open(scene) as scene_image:
        output_height = round(OUTPUT_WIDTH * scene_image.height / scene_image.width)
    with Image.open(generated) as image:
        final = ImageOps.fit(
            image.convert("RGB"),
            (OUTPUT_WIDTH, output_height),
            method=Image.Resampling.LANCZOS,
        )
        final = ImageEnhance.Contrast(final).enhance(1.12)
        final.save(output, optimize=True)


def create_print_raster(image_path, output_path):
    with Image.open(image_path) as image:
        raster = receipt_raster(
            image,
            384,
            config["printer_threshold"],
            config["printer_dither"],
            config["print_height_scale"],
        )
    raster.save(output_path, optimize=True)
    return raster


def print_raster(raster):
    with printer_lock:
        check_cancelled()
        configure_printer()
        send_to_printer(
            raster,
            Bridge,
            config["printer_feed_lines"],
            cancelled=process_cancel_event.is_set,
        )


def configure_printer():
    accepted = Bridge.call(
        "print_configure",
        config["printer_heat_dots"],
        config["printer_heat_time"],
        config["printer_heat_interval"],
        config["printer_density"],
        config["printer_break_time"],
    )
    if accepted is not True:
        raise RuntimeError("Printer rejected its heat or density settings")


def printable_ticket_text(value, limit):
    text = " ".join(str(value).split())
    return text.encode("ascii", errors="replace").decode("ascii")[:limit]


def description_raster(description):
    width = 384
    margin = 16
    body_font = ImageFont.load_default(size=22)
    measure = ImageDraw.Draw(Image.new("1", (1, 1), 1))
    lines = []
    current = ""
    for word in printable_ticket_text(description, 600).split():
        candidate = f"{current} {word}".strip()
        if current and measure.textlength(candidate, font=body_font) > width - 2 * margin:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    line_height = 28
    image = Image.new("1", (width, 16 + line_height * len(lines)), 1)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((margin, 8 + index * line_height), line, font=body_font, fill=0)
    return image


def print_wifi_ticket(title, ssid="", psk="", message=""):
    with printer_lock:
        configure_printer()
        accepted = Bridge.call(
            "print_wifi_ticket",
            printable_ticket_text(title, 32),
            printable_ticket_text(ssid, 32),
            printable_ticket_text(psk, 128),
            printable_ticket_text(message, 160),
        )
        if accepted is not True:
            raise RuntimeError("Printer rejected the Wi-Fi ticket")


def print_description(description):
    print_raster(description_raster(description))


def describe_scene(scene):
    for attempt in range(2):
        chunks = []
        for chunk in vlm.chat_stream(message=VLM_PROMPT, images=[str(scene)]):
            chunks.append(chunk)
        description = "".join(chunks).strip()
        if description:
            return description
        if attempt == 0:
            logger.info("Local VLM returned an empty cold-start response; retrying")
    return ""


def set_accelerator_mode(mode, model="standard"):
    parsed = urllib.parse.urlparse(config["npu_url"])
    mode_url = urllib.parse.urlunparse(parsed._replace(path="/mode", query=""))
    request = urllib.request.Request(
        mode_url,
        data=json.dumps({"mode": mode, "model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    if result.get("status") != "ready":
        raise RuntimeError(f"Accelerator did not enter {mode} mode")


def model_service_status():
    try:
        return request_model_service("status", timeout=5)
    except (OSError, RuntimeError, socket.timeout):
        return "unavailable"


def model_service_worker(action, client):
    try:
        status = request_model_service(action)
        if action == "shutdown":
            with state_lock:
                state["model_service"] = {
                    "status": status,
                    "action": "shutting down",
                }
            ui.send_message("camera_state", snapshot_state())
            return
    except (OSError, RuntimeError, socket.timeout) as exc:
        logger.error(f"Model service {action} failed: {exc}")
        ui.send_message("service_error", {"message": str(exc)}, client)
    finally:
        with state_lock:
            state["model_service"] = {
                "status": model_service_status(),
                "action": None,
            }
        refresh_availability()
        ui.send_message("camera_state", snapshot_state())


def on_model_service(client, data):
    action = str((data or {}).get("action", "")).strip()
    if action != "shutdown":
        ui.send_message("service_error", {"message": "Unknown model service action"}, client)
        return
    with state_lock:
        if state["busy"]:
            ui.send_message(
                "service_error",
                {"message": "Model service cannot change while a photo is processing"},
                client,
            )
            return
        if state["model_service"]["action"]:
            return
        state["model_service"] = {
            "status": state["model_service"]["status"],
            "action": "shutting down",
        }
    ui.send_message("camera_state", snapshot_state())
    threading.Thread(
        target=model_service_worker,
        args=(action, client),
        daemon=True,
    ).start()


def try_print_wifi_ticket(title, ssid="", psk="", message=""):
    try:
        print_wifi_ticket(title, ssid, psk, message)
    except Exception as exc:
        logger.error(f"Wi-Fi ticket could not be printed: {exc}")


def set_active_profile(profile_id, mode):
    with state_lock:
        state["active_mode"] = mode
        state["active_gesture"] = profile_id
    ui.send_message("camera_state", snapshot_state())
    try:
        button_index = PROFILE_IDS.index(profile_id) % 3 if profile_id else -1
        Bridge.call("set_active_mode", button_index)
    except Exception as exc:
        logger.info(f"Button mode update skipped: {exc}")


def process_capture(profile_id, profile, source="hardware"):
    mode = profile["mode"]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    stem = f"{stamp}-{mode}"
    original = CAPTURES_DIR / f"{stem}-original.jpg"
    scene = CAPTURES_DIR / f"{stem}-scene.jpg"
    generated = CAPTURES_DIR / f"{stem}-generated.png"
    final = CAPTURES_DIR / f"{stem}-final.png"
    print_image = CAPTURES_DIR / f"{stem}-print.png"
    try:
        if mode not in MODE_NAMES:
            raise RuntimeError(f"Unknown capture mode: {mode}")
        if not state["availability"].get(profile_id, False):
            raise RuntimeError(f"{profile_id.replace('_', ' ').upper()} is not currently available")
        set_active_profile(profile_id, mode)
        publish("countdown", f"Picture requested from {source}", True)
        wait_interruptibly(0.6)
        publish("capture", "Capturing scene", True)
        try:
            if Bridge.call("capture_light", True) is not True:
                raise RuntimeError("Capture light did not turn on")
            capture_frame(original)
        finally:
            try:
                Bridge.call("capture_light", False)
            except Exception as exc:
                logger.error(f"Capture light did not turn off cleanly: {exc}")
            check_cancelled()
        prepare_scene(original, scene, profile)
        reference_path = profile_reference_path(profile_id)
        if mode == "describe":
            publish("generating", "Preparing the local VLM", True)
            restore_image_model = model_service_status() == "active"
            if restore_image_model:
                set_accelerator_mode("describe")
            try:
                publish("generating", "Describing the scene with the local VLM", True)
                description = run_interruptibly(lambda: describe_scene(scene))
            finally:
                if restore_image_model:
                    publish("generating", "Restoring the local image model", True)
                    set_accelerator_mode("local")
            check_cancelled()
            if not description:
                raise RuntimeError("The local VLM returned an empty description")
            scene.unlink(missing_ok=True)
            if config["printer_enabled"]:
                publish("printing", "Printing the scene description", True)
                print_description(description)
            publish("done", "Scene description printed", False)
            wait_interruptibly(1.5)
            publish("ready", "Ready", False)
            return
        if mode == "normal":
            publish("generating", "Preparing the normal photo", True)
            shutil.copyfile(scene, generated)
        elif mode == "cloud":
            publish("generating", "Composing with Nano Banana", True)
            generate_openrouter(scene, generated, profile, reference_path)
        else:
            publish("generating", "Applying the local AI effect", True)
            generate_npu(scene, generated, profile, reference_path)
        finish_image(generated, scene, final)
        raster = create_print_raster(final, print_image)
        scene.unlink(missing_ok=True)
        generated.unlink(missing_ok=True)
        if config["printer_enabled"]:
            publish("printing", "Printing your instant photo", True)
            print_raster(raster)
        publish("done", "Your instant photo is ready", False)
        wait_interruptibly(1.5)
        publish("ready", "Ready", False)
    except InterruptedError:
        logger.info("Active process cancelled")
        for path in (original, scene, generated, final, print_image):
            path.unlink(missing_ok=True)
        publish("ready", "Ready", False)
    except Exception as exc:
        if process_cancel_event.is_set():
            logger.info("Active process cancelled")
            for path in (original, scene, generated, final, print_image):
                path.unlink(missing_ok=True)
            publish("ready", "Ready", False)
        else:
            logger.error(str(exc))
            publish("error", str(exc), False)
            time.sleep(2)
            publish("ready", "Ready", False)
    finally:
        set_active_profile(None, None)
        process_cancel_event.clear()


def worker():
    while True:
        profile_id, profile, source = jobs.get()
        process_capture(profile_id, profile, source)
        jobs.task_done()


def request_capture(profile_id, source):
    with state_lock:
        if state["busy"] or profile_id not in PROFILE_IDS:
            return False
        try:
            process_cancel_event.clear()
            profile = copy.deepcopy(config["button_profiles"][profile_id])
            jobs.put_nowait((profile_id, profile, source))
            state["busy"] = True
            return True
        except queue.Full:
            state["busy"] = False
            return False


def on_hardware_shutter(profile_id="a_short"):
    request_capture(str(profile_id), "Modulino button")


def wifi_setup_worker():
    credentials = None
    try:
        publish("wifi_scan", "Hold the Wi-Fi sharing QR code in front of the camera", True)
        deadline = time.monotonic() + config["wifi_scan_timeout"]
        camera_device = resolve_camera_device()
        if camera_device is None:
            raise RuntimeError("USB webcam is not connected")
        credentials = scan_wifi_qr(
            camera_device,
            deadline,
            wifi_cancel_event,
            logger.info,
        )
        if wifi_cancel_event.is_set():
            logger.info("Wi-Fi setup cancelled")
            return
        if credentials is None:
            raise RuntimeError("Wi-Fi QR scan timed out")
        logger.info(f"Wi-Fi QR decoded: SSID={credentials['ssid']!r}")
        try_print_wifi_ticket(
            "WIFI QR SCANNED",
            credentials["ssid"],
            credentials["password"],
            "Connecting...",
        )
        publish("wifi_connect", "Connecting to the scanned Wi-Fi network", True)
        connected_ssid = connect_wifi(
            credentials,
            cancel_event=process_cancel_event,
        )
        success_message = f"Wi-Fi connection successful: SSID={connected_ssid!r}"
        logger.info(success_message)
        try_print_wifi_ticket(
            "WIFI CONNECTED",
            connected_ssid,
            message="Connection successful",
        )
        publish("wifi_success", "Wi-Fi connected", True)
        process_cancel_event.wait(3)
    except InterruptedError:
        logger.info("Wi-Fi setup cancelled")
    except Exception as exc:
        failure_message = f"Wi-Fi connection or setup failed: {exc}"
        logger.error(failure_message)
        try_print_wifi_ticket(
            "WIFI CONNECTION FAILED",
            credentials["ssid"] if credentials else "",
            message=str(exc),
        )
        publish("wifi_error", str(exc), True)
        process_cancel_event.wait(3)
    finally:
        wifi_cancel_event.clear()
        process_cancel_event.clear()
        publish("ready", "Ready", False)


def on_wifi_setup():
    with state_lock:
        if state["busy"]:
            return
        state["busy"] = True
        wifi_cancel_event.clear()
        process_cancel_event.clear()
    threading.Thread(target=wifi_setup_worker, daemon=True).start()


def on_cancel_process():
    with state_lock:
        if not state["busy"]:
            return
        state["message"] = "Cancelling"
    process_cancel_event.set()
    wifi_cancel_event.set()
    vlm.stop_stream()
    ui.send_message("camera_state", snapshot_state())


def on_wifi_cancel():
    on_cancel_process()


def on_ui_capture(_client, data):
    request_capture(str((data or {}).get("profile_id", "a_short")), "web control")
    ui.send_message("camera_state", snapshot_state())


def on_ui_cancel(_client, _data):
    on_cancel_process()


def on_test_print(client, _data):
    try:
        with state_lock:
            if state["busy"]:
                raise ValueError("Printer test cannot run while a photo is processing")
        publish("printing", "Printing diagnostic ticket", True)
        with printer_lock:
            configure_printer()
            if Bridge.call("print_test") is not True:
                raise ValueError("Printer rejected the test")
        publish("done", "Diagnostic ticket printed", False)
        time.sleep(1)
        publish("ready", "Ready", False)
    except (RuntimeError, ValueError) as exc:
        publish("error", str(exc), False)
        ui.send_message("settings_error", {"message": str(exc)}, client)
        time.sleep(2)
        publish("ready", "Ready", False)


def on_reprint(client, data):
    try:
        with state_lock:
            if state["busy"]:
                raise ValueError("Reprint cannot run while another job is processing")
        photo_id = Path(str(data.get("id", ""))).name
        print_path = CAPTURES_DIR / f"{photo_id}-print.png"
        if not print_path.is_file():
            raise ValueError("That print is no longer available")
        publish("printing", "Reprinting archived photo", True)
        with Image.open(print_path) as image:
            print_raster(image.convert("1"))
        publish("done", "Archived photo reprinted", False)
        time.sleep(1)
        publish("ready", "Ready", False)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        publish("error", str(exc), False)
        ui.send_message("settings_error", {"message": str(exc)}, client)
        time.sleep(2)
        publish("ready", "Ready", False)


def on_get_state(client, _data):
    ui.send_message("camera_state", snapshot_state(), client)


def on_set_settings(client, data):
    try:
        profile_id = str(data.get("profile_id", "")).strip()
        mode = str(data.get("mode", "")).strip()
        npu_model = str(data.get("npu_model", "")).strip()
        prompt = str(data.get("prompt", "")).strip()
        cloud_prompt = str(data.get("cloud_prompt", "")).strip()
        openrouter_model = str(data.get("openrouter_model", "")).strip()
        submitted_api_key = str(data.get("openrouter_api_key", "")).strip()
        remove_api_key = bool(data.get("remove_openrouter_api_key"))
        resolution = int(data.get("resolution"))
        steps = int(data.get("steps"))
        guidance_scale = float(data.get("guidance_scale"))
        image_guidance_scale = float(data.get("image_guidance_scale"))
        seed_value = data.get("seed")
        seed = None if seed_value in (None, "") else int(seed_value)
        camera_brightness = float(data.get("camera_brightness"))
        camera_contrast = float(data.get("camera_contrast"))
        printer_enabled = bool(data.get("printer_enabled"))
        printer_threshold = int(data.get("printer_threshold"))
        printer_dither = bool(data.get("printer_dither"))
        printer_feed_lines = int(data.get("printer_feed_lines"))
        printer_heat_dots = int(data.get("printer_heat_dots"))
        printer_heat_time = int(data.get("printer_heat_time"))
        printer_heat_interval = int(data.get("printer_heat_interval"))
        printer_density = int(data.get("printer_density"))
        printer_break_time = int(data.get("printer_break_time"))
        if profile_id not in PROFILE_IDS:
            raise ValueError("Unknown button gesture")
        if mode not in MODE_NAMES:
            raise ValueError("Mode must be Normal, Local, Cloud, or Describe")
        if npu_model != "standard":
            raise ValueError("Local model must be standard")
        if not prompt or len(prompt) > 500:
            raise ValueError("Prompt must contain 1 to 500 characters")
        if not cloud_prompt or len(cloud_prompt) > 800:
            raise ValueError("Cloud prompt must contain 1 to 800 characters")
        if "/" not in openrouter_model or len(openrouter_model) > 100:
            raise ValueError("OpenRouter model must be a provider/model ID")
        if submitted_api_key and (len(submitted_api_key) < 20 or len(submitted_api_key) > 512 or any(character.isspace() for character in submitted_api_key)):
            raise ValueError("OpenRouter API key must contain 20 to 512 characters without spaces")
        if submitted_api_key and remove_api_key:
            raise ValueError("Cannot save and remove the OpenRouter API key together")
        if resolution not in (256, 384, 512):
            raise ValueError("Input resolution must be 256, 384, or 512")
        expected_resolution = NPU_MODEL_DEFAULTS[npu_model]["resolution"]
        if resolution != expected_resolution:
            raise ValueError(
                f"{npu_model.title()} model requires {expected_resolution}px input"
            )
        if not 4 <= steps <= 30:
            raise ValueError("Steps must be between 4 and 30")
        if not 1.0 <= guidance_scale <= 15.0:
            raise ValueError("Text guidance must be between 1 and 15")
        if not 1.0 <= image_guidance_scale <= 3.0:
            raise ValueError("Image guidance must be between 1 and 3")
        if seed is not None and not 0 <= seed <= 0x7FFFFFFF:
            raise ValueError("Seed must be between 0 and 2147483647")
        if not -1.0 <= camera_brightness <= 1.0:
            raise ValueError("Camera brightness must be between -1 and 1")
        if not 0.5 <= camera_contrast <= 2.0:
            raise ValueError("Camera contrast must be between 0.5 and 2")
        if not 1 <= printer_threshold <= 254:
            raise ValueError("Printer threshold must be between 1 and 254")
        if not 0 <= printer_feed_lines <= 8:
            raise ValueError("Printer feed must be between 0 and 8 lines")
        if not 1 <= printer_heat_dots <= 30:
            raise ValueError("Heat dots must be between 1 and 30")
        if not 3 <= printer_heat_time <= 255:
            raise ValueError("Heat time must be between 3 and 255")
        if not 0 <= printer_heat_interval <= 255:
            raise ValueError("Heat interval must be between 0 and 255")
        if not 0 <= printer_density <= 20:
            raise ValueError("Print density must be between 0 and 20")
        if not 0 <= printer_break_time <= 7:
            raise ValueError("Break time must be between 0 and 7")
        with state_lock:
            if state["busy"]:
                raise ValueError("Settings cannot change while a photo is processing")
            config["button_profiles"][profile_id] = {
                "mode": mode,
                "npu_model": npu_model,
                "prompt": prompt,
                "resolution": resolution,
                "steps": steps,
                "guidance_scale": guidance_scale,
                "image_guidance_scale": image_guidance_scale,
                "seed": seed,
                "cloud_prompt": cloud_prompt,
                "openrouter_model": openrouter_model,
            }
            config.update({
                "camera_brightness": camera_brightness,
                "camera_contrast": camera_contrast,
                "printer_enabled": printer_enabled,
                "printer_threshold": printer_threshold,
                "printer_dither": printer_dither,
                "printer_feed_lines": printer_feed_lines,
                "printer_heat_dots": printer_heat_dots,
                "printer_heat_time": printer_heat_time,
                "printer_heat_interval": printer_heat_interval,
                "printer_density": printer_density,
                "printer_break_time": printer_break_time,
            })
            saved_config = dict(config)
        temporary_config = CONFIG_PATH.with_suffix(".json.tmp")
        temporary_config.write_text(json.dumps(saved_config, indent=2) + "\n", encoding="utf-8")
        temporary_config.replace(CONFIG_PATH)
        if submitted_api_key:
            save_openrouter_api_key(submitted_api_key)
        elif remove_api_key:
            save_openrouter_api_key("")
        refresh_availability()
        ui.send_message("camera_state", snapshot_state())
    except (OSError, TypeError, ValueError) as exc:
        ui.send_message("settings_error", {"message": str(exc)}, client)


def on_set_reference(client, data):
    try:
        with state_lock:
            if state["busy"]:
                raise ValueError("Reference cannot change while a photo is processing")
        profile_id = str(data.get("profile_id", ""))
        if profile_id not in PROFILE_IDS:
            raise ValueError("Unknown button gesture")
        reference_path = profile_reference_path(profile_id)
        data_url = str(data.get("data_url", ""))
        if not data_url:
            reference_path.unlink(missing_ok=True)
        else:
            if not data_url.startswith("data:image/") or "," not in data_url:
                raise ValueError("Reference must be an image")
            encoded = data_url.split(",", 1)[1]
            if len(encoded) > 12 * 1024 * 1024:
                raise ValueError("Reference image is too large")
            image_bytes = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(image_bytes)) as image:
                reference = ImageOps.exif_transpose(image).convert("RGB")
                reference.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                reference_path.parent.mkdir(parents=True, exist_ok=True)
                reference.save(reference_path, format="JPEG", quality=92)
        ui.send_message("camera_state", snapshot_state())
    except (OSError, TypeError, ValueError) as exc:
        ui.send_message("settings_error", {"message": str(exc)}, client)


def endpoint_reachable(url, timeout=1.0):
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def available_npu_models():
    parsed = urllib.parse.urlparse(config["npu_url"])
    health_url = urllib.parse.urlunparse(parsed._replace(path="/health", query=""))
    try:
        with urllib.request.urlopen(health_url, timeout=1.5) as response:
            health = json.load(response)
        if health.get("status") == "ready":
            return set(health.get("models", []))
    except (OSError, TypeError, ValueError, urllib.error.URLError):
        pass
    return set()


def genie_service_ready():
    try:
        with urllib.request.urlopen(
            "http://genie-models-runner:9001/v1/health",
            timeout=1.5,
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def refresh_availability():
    camera_device = resolve_camera_device()
    camera_ready = camera_device is not None
    cloud_ready = bool(openrouter_api_key()) and endpoint_reachable("https://openrouter.ai", 1.5)
    service_status = model_service_status()
    with state_lock:
        service_action = state["model_service"]["action"]
    npu_models = available_npu_models() if service_status == "active" and not service_action else set()
    describe_ready = bool(npu_models) or genie_service_ready()
    availability = {}
    for profile_id, profile in config["button_profiles"].items():
        if profile["mode"] == "normal":
            availability[profile_id] = camera_ready
        elif profile["mode"] == "describe":
            availability[profile_id] = camera_ready and describe_ready
        elif profile["mode"] == "cloud":
            availability[profile_id] = camera_ready and cloud_ready and profile_reference_path(profile_id).is_file()
        else:
            availability[profile_id] = camera_ready and profile["npu_model"] in npu_models
    with state_lock:
        changed = availability != state["availability"]
        state["availability"] = availability
        state["camera"] = camera_device or "USB webcam not connected"
        state["model_service"] = {
            "status": service_status,
            "action": service_action,
        }
    try:
        availability_mask = sum(
            (1 << index) for index, profile_id in enumerate(PROFILE_IDS)
            if availability[profile_id]
        )
        Bridge.call("set_options", availability_mask)
    except Exception as exc:
        logger.info(f"Button availability update skipped: {exc}")
    if changed:
        ui.send_message("camera_state", snapshot_state())


def availability_worker():
    time.sleep(1)
    publish("ready", "Ready", False)
    while True:
        refresh_availability()
        time.sleep(10)


CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
Bridge.provide("take_photo", on_hardware_shutter)
Bridge.provide("wifi_setup", on_wifi_setup)
Bridge.provide("wifi_cancel", on_wifi_cancel)
Bridge.provide("cancel_process", on_cancel_process)
ui.on_message("take_photo", on_ui_capture)
ui.on_message("cancel_process", on_ui_cancel)
ui.on_message("test_print", on_test_print)
ui.on_message("reprint", on_reprint)
ui.on_message("get_state", on_get_state)
ui.on_message("set_settings", on_set_settings)
ui.on_message("set_reference", on_set_reference)
ui.on_message("model_service", on_model_service)
threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=availability_worker, daemon=True).start()
App.run()