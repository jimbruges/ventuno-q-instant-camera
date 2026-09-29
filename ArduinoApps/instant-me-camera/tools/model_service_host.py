#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent
SOCKET_PATH = APP_DIR / ".model-service.sock"
MODEL_SERVICE_UNIT = "instant-camera-npu.service"
ASSOCIATED_SERVICES = (
    MODEL_SERVICE_UNIT,
    "instant-camera-wifi.service",
)
ASSOCIATED_APPS = (
    Path.home() / "ArduinoApps" / "instant-me-camera",
    Path.home() / "ArduinoApps" / "instant-camera",
)


def service_status():
    result = subprocess.run(
        ["systemctl", "--user", "is-active", MODEL_SERVICE_UNIT],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def run_shutdown():
    time.sleep(1)
    subprocess.run(
        ["systemctl", "--user", "stop", *ASSOCIATED_SERVICES],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    for app_path in ASSOCIATED_APPS:
        if app_path.is_dir():
            subprocess.run(
                ["arduino-app-cli", "app", "stop", str(app_path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
    subprocess.run(
        ["systemctl", "--user", "stop", "instant-camera-model-control.service"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def control(request):
    action = request.get("action")
    if action not in {"status", "shutdown"}:
        raise ValueError("Unknown model service action")
    if action == "shutdown":
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--shutdown"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Application shutdown requested", flush=True)
        return {"ok": True, "status": "shutting_down"}
    return {"ok": True, "status": service_status()}


def handle(connection):
    try:
        connection.settimeout(135)
        data = b""
        while b"\n" not in data and len(data) < 16384:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
        response = control(json.loads(data.decode("utf-8")))
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, socket.timeout, subprocess.TimeoutExpired) as exc:
        print(f"Model service action failed: {exc}", flush=True)
        response = {"ok": False, "error": str(exc)}
    try:
        connection.sendall(json.dumps(response).encode("utf-8") + b"\n")
    except (BrokenPipeError, ConnectionResetError):
        pass


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--shutdown":
        run_shutdown()
        return
    SOCKET_PATH.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o600)
        server.listen(2)
        while True:
            connection, _ = server.accept()
            with connection:
                handle(connection)


if __name__ == "__main__":
    main()