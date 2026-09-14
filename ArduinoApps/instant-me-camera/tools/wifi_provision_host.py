#!/usr/bin/env python3
import json
import os
import socket
import subprocess
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent
SOCKET_PATH = APP_DIR / ".wifi-provision.sock"


def wireless_interface():
    result = subprocess.run(
        ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    for line in result.stdout.splitlines():
        device, _, device_type = line.partition(":")
        if device_type == "wifi":
            return device
    raise RuntimeError("No Wi-Fi adapter is available")


def saved_wifi_profile(ssid):
    result = subprocess.run(
        ["nmcli", "-t", "-f", "UUID,TYPE", "connection", "show"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    for line in result.stdout.splitlines():
        uuid, _, connection_type = line.partition(":")
        if connection_type != "802-11-wireless":
            continue
        profile = subprocess.run(
            ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", uuid],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if profile.stdout.rstrip("\n") == ssid:
            return uuid
    return None


def connect(request):
    ssid = request.get("ssid")
    password = request.get("password", "")
    security = request.get("security")
    hidden = request.get("hidden") is True
    if not isinstance(ssid, str) or not ssid or len(ssid.encode("utf-8")) > 32:
        raise ValueError("Invalid network name")
    if security not in {"nopass", "wep", "wpa"}:
        raise ValueError("Unsupported network security")
    if not isinstance(password, str) or len(password) > 128:
        raise ValueError("Invalid network password")
    if security != "nopass" and not password:
        raise ValueError("Network password is required")
    interface = wireless_interface()
    saved_profile = saved_wifi_profile(ssid)
    if saved_profile:
        command = [
            "nmcli", "--wait", "40", "connection", "up", "uuid", saved_profile,
            "ifname", interface,
        ]
    else:
        command = [
            "nmcli", "--wait", "40", "device", "wifi", "connect", ssid,
            "ifname", interface,
        ]
    if not saved_profile and security != "nopass":
        command.extend(["password", password])
    if not saved_profile and security == "wep":
        command.extend(["wep-key-type", "key"])
    if not saved_profile and hidden:
        command.extend(["hidden", "yes"])
    print(f"Wi-Fi connection requested: SSID={ssid!r}", flush=True)
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "NetworkManager could not connect to that network")
    print(f"Wi-Fi connection successful: SSID={ssid!r}", flush=True)
    return {"ok": True, "ssid": ssid}


def handle(connection):
    try:
        connection.settimeout(5)
        data = b""
        while b"\n" not in data and len(data) < 16384:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
        request = json.loads(data.decode("utf-8"))
        response = connect(request)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, socket.timeout) as exc:
        print(f"Wi-Fi connection failed: {exc}", flush=True)
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