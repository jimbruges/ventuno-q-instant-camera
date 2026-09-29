import json
import socket
from pathlib import Path


SOCKET_PATH = Path("/app/.model-service.sock")


def request_model_service(action, socket_path=SOCKET_PATH, timeout=135):
    if action not in {"status", "shutdown"}:
        raise ValueError("Unknown model service action")
    request = json.dumps({"action": action}).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall(request)
        response_data = b""
        while b"\n" not in response_data and len(response_data) < 16384:
            chunk = client.recv(4096)
            if not chunk:
                break
            response_data += chunk
    try:
        response = json.loads(response_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Model service helper returned an invalid response") from exc
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Model service action failed"))
    return str(response.get("status", "unknown"))