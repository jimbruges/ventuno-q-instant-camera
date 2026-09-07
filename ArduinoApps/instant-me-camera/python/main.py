import base64
import io
import json
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

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge, Logger
from receipt_effects import receipt_raster, send_to_printer


APP_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_DIR / "assets"
CAPTURES_DIR = ASSETS_DIR / "captures"
REFERENCE_PATH = ASSETS_DIR / "reference" / "me.jpg"
NPU_REFERENCE_PATH = ASSETS_DIR / "reference" / "object.jpg"
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_PROMPT = "same room, candid realistic instant camera photograph, one large yellow rubber duck centered on the floor in the masked area"
DEFAULT_NPU_PROMPT = "Please replace the just the head of any human in this image with a large rubber duck. Preserve every person, their clothes, the room, lighting, and camera angle."
DEFAULT_CLOUD_PROMPT = "Place the person from the second reference image naturally into the scene in the first image. Keep their face, body, clothes, and identity recognizable. If people are present, pose them together; otherwise place the person naturally in the background. Preserve the scene, lighting, camera angle, and realistic photographic style."
OUTPUT_WIDTH = 200
MODE_NAMES = {"normal": 0, "cloud": 1, "local": 2}

logger = Logger("InstantMeCamera")
ui = WebUI()
jobs = queue.Queue(maxsize=1)
state_lock = threading.Lock()
printer_lock = threading.Lock()
state = {
    "status": "ready",
    "message": "Ready",
    "backend": "preview",
    "camera": "/dev/video0",
    "busy": False,
    "active_mode": None,
    "availability": {"normal": False, "cloud": False, "local": False},
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
        "cloud_prompt": os.getenv("CLOUD_PROMPT", DEFAULT_CLOUD_PROMPT),
        "identity_prompt": os.getenv("IDENTITY_PROMPT", "candid instant camera photograph, a man img naturally joining the people in the scene"),
        "sd_cli": os.getenv("SD_CLI", str(Path.home() / "instant-camera-ai" / "bin" / "sd-cli")),
        "sd_model": os.getenv("SD_MODEL", str(Path.home() / "instant-camera-ai" / "models" / "realistic-vision-v5.1.safetensors")),
        "sd_lora": os.getenv("SD_LORA", str(Path.home() / "instant-camera-ai" / "models" / "hyper-sd15-4step.safetensors")),
        "photo_maker": os.getenv("PHOTO_MAKER_MODEL", str(Path.home() / "instant-camera-ai" / "models" / "photomaker-v1.safetensors")),
        "identity_model": os.getenv("IDENTITY_MODEL", str(Path.home() / "instant-camera-ai" / "models" / "sdxl-lightning.safetensors")),
        "generation_width": int(os.getenv("GENERATION_WIDTH", "384")),
        "generation_steps": int(os.getenv("GENERATION_STEPS", "4")),
        "generation_strength": float(os.getenv("GENERATION_STRENGTH", "0.78")),
        "camera_rotation": int(os.getenv("CAMERA_ROTATION", "0")),
        "openrouter_model": os.getenv("OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image"),
        "camera_brightness": float(os.getenv("CAMERA_BRIGHTNESS", "0")),
        "camera_contrast": float(os.getenv("CAMERA_CONTRAST", "1")),
        "printer_enabled": os.getenv("PRINTER_ENABLED", "true").lower() not in {"0", "false", "no"},
        "printer_threshold": int(os.getenv("PRINTER_THRESHOLD", "125")),
        "printer_feed_lines": int(os.getenv("PRINTER_FEED_LINES", "3")),
        "printer_heat_dots": int(os.getenv("PRINTER_HEAT_DOTS", "5")),
        "printer_heat_time": int(os.getenv("PRINTER_HEAT_TIME", "162")),
        "printer_heat_interval": int(os.getenv("PRINTER_HEAT_INTERVAL", "40")),
        "printer_density": int(os.getenv("PRINTER_DENSITY", "4")),
        "printer_break_time": int(os.getenv("PRINTER_BREAK_TIME", "2")),
    }
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return config


config = load_config()
state["backend"] = config["backend"]
state["camera"] = config["camera"]


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
        result["reference_ready"] = REFERENCE_PATH.exists()
        result["npu_reference"] = (
            f"reference/{NPU_REFERENCE_PATH.name}?v={NPU_REFERENCE_PATH.stat().st_mtime_ns}"
            if NPU_REFERENCE_PATH.exists()
            else None
        )
        result["settings"] = {
            "prompt": config["npu_prompt"],
            "cloud_prompt": config["cloud_prompt"],
            "openrouter_model": config["openrouter_model"],
            "resolution": config["generation_width"],
            "steps": config["npu_steps"],
            "guidance_scale": config["npu_guidance_scale"],
            "image_guidance_scale": config["npu_image_guidance_scale"],
            "seed": config["npu_seed"],
            "camera_brightness": config["camera_brightness"],
            "camera_contrast": config["camera_contrast"],
            "printer_enabled": config["printer_enabled"],
            "printer_threshold": config["printer_threshold"],
            "printer_feed_lines": config["printer_feed_lines"],
            "printer_heat_dots": config["printer_heat_dots"],
            "printer_heat_time": config["printer_heat_time"],
            "printer_heat_interval": config["printer_heat_interval"],
            "printer_density": config["printer_density"],
            "printer_break_time": config["printer_break_time"],
        }
        result["openrouter_key_ready"] = bool(os.getenv("OPENROUTER_API_KEY"))
        required = []
        if config["backend"] == "local":
            required.extend([config["sd_cli"], config["sd_model"], config["sd_lora"]])
        elif config["backend"] == "identity":
            required.extend([config["sd_cli"], config["identity_model"], config["photo_maker"]])
        result["local_ready"] = all(Path(path).is_file() for path in required)
        return result


def publish(status, message, busy=None):
    with state_lock:
        state["status"] = status
        state["message"] = message
        if busy is not None:
            state["busy"] = busy
    try:
        Bridge.notify("camera_status", status)
    except Exception as exc:
        logger.info(f"Matrix status update skipped: {exc}")
    ui.send_message("camera_state", snapshot_state())


def capture_frame(destination):
    bundled_ffmpeg = APP_DIR / "tools" / "ffmpeg"
    ffmpeg_args = [
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "640x480",
        "-i", config["camera"],
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
            "docker", "run", "--rm", "--device", config["camera"],
            "-v", f"{destination.parent}:/output", "alpine:3.21",
            "sh", "-c", "apk add --no-cache ffmpeg >/dev/null && exec ffmpeg \"$@\"",
            "ffmpeg", *ffmpeg_args[:-1], container_output,
        ]
    else:
        raise RuntimeError("Camera capture requires ffmpeg or Docker")
    try:
        subprocess.run(command, check=True, timeout=15)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Camera capture failed: {exc}") from exc


def prepare_scene(source, destination):
    with Image.open(source) as image:
        prepared = image.convert("RGB")
        if config["camera_rotation"]:
            prepared = prepared.rotate(config["camera_rotation"], expand=True)
        generation_width = config["generation_width"]
        generation_height = round(generation_width * prepared.height / prepared.width)
        prepared.resize(
            (generation_width, generation_height), Image.Resampling.LANCZOS
        ).save(destination, quality=92)


def generate_local(scene, output):
    lora_path = Path(config["sd_lora"])
    prompt = f"{config['prompt']} <lora:{lora_path.stem}:1>"
    mask_path = output.with_suffix(".mask.png")
    with Image.open(scene) as scene_image:
        generation_size = scene_image.size
    mask = Image.new("L", generation_size, 0)
    width, height = mask.size
    ImageDraw.Draw(mask).ellipse((width * 0.07, height * 0.35, width * 0.60, height * 1.10), fill=255)
    mask.filter(ImageFilter.GaussianBlur(max(3, width // 64))).save(mask_path)
    try:
        command = [
            config["sd_cli"], "-m", config["sd_model"],
            "--lora-model-dir", str(lora_path.parent),
            "--init-img", str(scene), "--mask", str(mask_path),
            "--strength", str(config["generation_strength"]),
            "--prompt", prompt,
            "--negative-prompt", "person, human, text, watermark, deformed, illustration",
            "--width", str(width), "--height", str(height),
            "--steps", str(config["generation_steps"]), "--cfg-scale", "1.0",
            "--sampling-method", "ddim_trailing", "--scheduler", "discrete",
            "--threads", "8", "--vae-tiling", "--output", str(output),
        ]
        subprocess.run(command, check=True, timeout=300)
    finally:
        mask_path.unlink(missing_ok=True)


def generate_npu(scene, output):
    seed = config["npu_seed"]
    payload = {
        "prompt": config["npu_prompt"],
        "image": base64.b64encode(scene.read_bytes()).decode("ascii"),
        "steps": config["npu_steps"],
        "guidance_scale": config["npu_guidance_scale"],
        "image_guidance_scale": config["npu_image_guidance_scale"],
        "seed": seed if seed is not None else time.time_ns() & 0x7FFFFFFF,
    }
    if NPU_REFERENCE_PATH.exists():
        payload["reference_image"] = base64.b64encode(
            NPU_REFERENCE_PATH.read_bytes()
        ).decode("ascii")
    request = urllib.request.Request(
        config["npu_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            if response.headers.get_content_type() != "image/png":
                raise RuntimeError("NPU service returned an invalid response")
            output.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"NPU service failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NPU service is unavailable: {exc.reason}") from exc


def generate_identity(scene, output):
    identity_dir = REFERENCE_PATH.parent
    with Image.open(scene) as scene_image:
        width, height = scene_image.size
    command = [
        config["sd_cli"], "-m", config["identity_model"],
        "--photo-maker", config["photo_maker"],
        "--pm-id-images-dir", str(identity_dir),
        "--pm-style-strength", "10",
        "--init-img", str(scene), "--strength", "0.72",
        "--prompt", config["identity_prompt"],
        "--negative-prompt", "color, text, watermark, malformed face, duplicate person",
        "--width", str(width), "--height", str(height),
        "--steps", "4", "--cfg-scale", "1.0", "--sampling-method", "euler",
        "--threads", "8", "--vae-tiling", "--output", str(output),
    ]
    subprocess.run(command, check=True, timeout=600)


def encode_data_url(path):
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def generate_openrouter(scene, output):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured in Brick Configuration")
    payload = {
        "model": config["openrouter_model"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": config["cloud_prompt"]},
            {"type": "image_url", "image_url": {"url": encode_data_url(scene)}},
            {"type": "image_url", "image_url": {"url": encode_data_url(REFERENCE_PATH)}},
        ]}],
        "modalities": ["image", "text"],
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
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


def finish_image(generated, output):
    with Image.open(generated) as image:
        output_height = round(OUTPUT_WIDTH * image.height / image.width)
        final = image.convert("RGB").resize(
            (OUTPUT_WIDTH, output_height), Image.Resampling.LANCZOS
        )
        final = ImageEnhance.Contrast(final).enhance(1.12)
        final.save(output, optimize=True)


def create_print_raster(image_path, output_path):
    with Image.open(image_path) as image:
        raster = receipt_raster(image, 384, config["printer_threshold"])
    raster.save(output_path, optimize=True)
    return raster


def print_raster(raster):
    with printer_lock:
        configure_printer()
        send_to_printer(raster, Bridge, config["printer_feed_lines"])


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


def set_active_mode(mode):
    with state_lock:
        state["active_mode"] = mode
    ui.send_message("camera_state", snapshot_state())
    try:
        Bridge.notify("set_active_mode", MODE_NAMES.get(mode, -1))
    except Exception as exc:
        logger.info(f"Button mode update skipped: {exc}")


def process_capture(mode="normal", source="hardware"):
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
        if not state["availability"].get(mode, False):
            raise RuntimeError(f"{mode.title()} mode is not currently available")
        set_active_mode(mode)
        publish("countdown", f"Picture requested from {source}", True)
        time.sleep(0.6)
        publish("capture", "Capturing scene", True)
        capture_frame(original)
        prepare_scene(original, scene)
        if mode == "normal":
            publish("generating", "Preparing the normal photo", True)
            shutil.copyfile(scene, generated)
        elif mode == "cloud":
            publish("generating", "Composing with Nano Banana", True)
            generate_openrouter(scene, generated)
        else:
            publish("generating", "Applying the local AI effect", True)
            generate_npu(scene, generated)
        finish_image(generated, final)
        raster = create_print_raster(generated, print_image)
        scene.unlink(missing_ok=True)
        generated.unlink(missing_ok=True)
        if config["printer_enabled"]:
            publish("printing", "Printing your instant photo", True)
            print_raster(raster)
        publish("done", "Your instant photo is ready", False)
        time.sleep(1.5)
        publish("ready", "Ready", False)
    except Exception as exc:
        logger.error(str(exc))
        publish("error", str(exc), False)
        time.sleep(2)
        publish("ready", "Ready", False)
    finally:
        set_active_mode(None)


def worker():
    while True:
        mode, source = jobs.get()
        process_capture(mode, source)
        jobs.task_done()


def request_capture(mode, source):
    with state_lock:
        if state["busy"]:
            return False
        try:
            jobs.put_nowait((mode, source))
            state["busy"] = True
            return True
        except queue.Full:
            state["busy"] = False
            return False


def on_hardware_shutter(mode="normal"):
    request_capture(str(mode), "Modulino button")


def on_ui_capture(_client, data):
    request_capture(str((data or {}).get("mode", "normal")), "web control")
    ui.send_message("camera_state", snapshot_state())


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
        prompt = str(data.get("prompt", "")).strip()
        cloud_prompt = str(data.get("cloud_prompt", "")).strip()
        openrouter_model = str(data.get("openrouter_model", "")).strip()
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
        printer_feed_lines = int(data.get("printer_feed_lines"))
        printer_heat_dots = int(data.get("printer_heat_dots"))
        printer_heat_time = int(data.get("printer_heat_time"))
        printer_heat_interval = int(data.get("printer_heat_interval"))
        printer_density = int(data.get("printer_density"))
        printer_break_time = int(data.get("printer_break_time"))
        if not prompt or len(prompt) > 500:
            raise ValueError("Prompt must contain 1 to 500 characters")
        if not cloud_prompt or len(cloud_prompt) > 800:
            raise ValueError("Cloud prompt must contain 1 to 800 characters")
        if not openrouter_model.startswith("google/") or len(openrouter_model) > 100:
            raise ValueError("OpenRouter model must be a Google model ID")
        if resolution not in (256, 384, 512):
            raise ValueError("Input resolution must be 256, 384, or 512")
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
            config.update({
                "npu_prompt": prompt,
                "cloud_prompt": cloud_prompt,
                "openrouter_model": openrouter_model,
                "generation_width": resolution,
                "npu_steps": steps,
                "npu_guidance_scale": guidance_scale,
                "npu_image_guidance_scale": image_guidance_scale,
                "npu_seed": seed,
                "camera_brightness": camera_brightness,
                "camera_contrast": camera_contrast,
                "printer_enabled": printer_enabled,
                "printer_threshold": printer_threshold,
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
        ui.send_message("camera_state", snapshot_state())
    except (OSError, TypeError, ValueError) as exc:
        ui.send_message("settings_error", {"message": str(exc)}, client)


def on_set_reference(client, data):
    try:
        with state_lock:
            if state["busy"]:
                raise ValueError("Reference cannot change while a photo is processing")
        data_url = str(data.get("data_url", ""))
        if not data_url:
            NPU_REFERENCE_PATH.unlink(missing_ok=True)
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
                NPU_REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
                reference.save(NPU_REFERENCE_PATH, format="JPEG", quality=92)
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


def refresh_availability():
    camera_ready = Path(config["camera"]).exists()
    availability = {
        "normal": camera_ready,
        "cloud": (
            camera_ready
            and REFERENCE_PATH.is_file()
            and bool(os.getenv("OPENROUTER_API_KEY"))
            and endpoint_reachable("https://openrouter.ai", 1.5)
        ),
        "local": camera_ready and endpoint_reachable(config["npu_url"]),
    }
    with state_lock:
        changed = availability != state["availability"]
        state["availability"] = availability
    try:
        Bridge.notify("set_options", availability["normal"], availability["cloud"], availability["local"])
    except Exception as exc:
        logger.info(f"Button availability update skipped: {exc}")
    if changed:
        ui.send_message("camera_state", snapshot_state())


def availability_worker():
    while True:
        refresh_availability()
        time.sleep(10)


CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
Bridge.provide("take_photo", on_hardware_shutter)
ui.on_message("take_photo", on_ui_capture)
ui.on_message("test_print", on_test_print)
ui.on_message("reprint", on_reprint)
ui.on_message("get_state", on_get_state)
ui.on_message("set_settings", on_set_settings)
ui.on_message("set_reference", on_set_reference)
threading.Thread(target=worker, daemon=True).start()
refresh_availability()
threading.Thread(target=availability_worker, daemon=True).start()
App.run()