#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent
SOCKET_PATH = APP_DIR / ".model-service.sock"
MODEL_SERVICE_UNIT = "instant-camera-npu.service"
GENIE_COMPOSE_PROJECT = "instant-me-camera"
GENIE_SERVICE = "genie-models-runner"


def service_status():
    result = subprocess.run(
        ["systemctl", "--user", "is-active", MODEL_SERVICE_UNIT],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def genie_container_name():
    result = subprocess.run(
        [
            "docker", "ps", "-a",
            "--filter", f"label=com.docker.compose.project={GENIE_COMPOSE_PROJECT}",
            "--filter", f"label=com.docker.compose.service={GENIE_SERVICE}",
            "--format", "{{.Names}}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    names = result.stdout.split()
    if len(names) != 1:
        raise RuntimeError(f"expected one Genie container, found {len(names)}")
    return names[0]


def wait_for_genie():
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:9001/v1/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise TimeoutError("Genie service did not become ready")


def start_app_service():
    subprocess.run(
        ["docker", "start", genie_container_name()],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    wait_for_genie()


def control(request):
    action = request.get("action")
    if action not in {"status", "start", "stop"}:
        raise ValueError("Unknown model service action")
    if action != "status":
        result = subprocess.run(
            ["systemctl", "--user", action, MODEL_SERVICE_UNIT],
            capture_output=True,
            text=True,
            timeout=130,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or "systemctl failed")
        if action == "stop":
            start_app_service()
        print(f"Model service {action} requested", flush=True)
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