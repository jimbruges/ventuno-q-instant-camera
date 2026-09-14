import json
import socket
import time
from pathlib import Path

import cv2
from pyzbar.pyzbar import ZBarSymbol, decode as decode_barcodes


SOCKET_PATH = Path("/app/.wifi-provision.sock")
SUPPORTED_SECURITY = {
    "": "nopass",
    "NOPASS": "nopass",
    "WEP": "wep",
    "WPA": "wpa",
    "WPA2": "wpa",
    "WPA3": "wpa",
    "SAE": "wpa",
}


def _split_escaped(value, delimiter):
    parts = []
    current = []
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == delimiter:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    parts.append("".join(current))
    return parts


def parse_wifi_qr(payload):
    if not payload.startswith("WIFI:"):
        raise ValueError("QR code is not an Android Wi-Fi sharing code")
    fields = {}
    for item in _split_escaped(payload[5:], ";"):
        if not item:
            continue
        key_value = _split_escaped(item, ":")
        if len(key_value) < 2:
            continue
        fields[key_value[0].upper()] = ":".join(key_value[1:])
    ssid = fields.get("S", "")
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise ValueError("Wi-Fi QR code has an invalid network name")
    security_name = fields.get("T", "").upper()
    if security_name not in SUPPORTED_SECURITY:
        raise ValueError(f"Unsupported Wi-Fi security type: {security_name}")
    security = SUPPORTED_SECURITY[security_name]
    password = fields.get("P", "")
    if security != "nopass" and not password:
        raise ValueError("Secured Wi-Fi QR code has no password")
    return {
        "ssid": ssid,
        "password": password,
        "security": security,
        "hidden": fields.get("H", "false").lower() == "true",
    }


def decode_wifi_qr(image_path):
    image = cv2.imread(str(image_path))
    if image is None:
        return None
    return decode_wifi_qr_frame(image)


def decode_wifi_qr_frame(image, detector=None):
    detector = detector or cv2.QRCodeDetector()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    for variant in (gray, clahe):
        for detection in decode_barcodes(variant, symbols=[ZBarSymbol.QRCODE]):
            try:
                payload = detection.data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            return parse_wifi_qr(payload)
    variants = [image, gray, clahe]
    variants.append(
        cv2.adaptiveThreshold(
            clahe,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            5,
        )
    )
    if min(gray.shape[:2]) < 1000:
        variants.extend(
            cv2.resize(variant, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            for variant in (gray, clahe)
        )
    for variant in variants:
        payload, points, _ = detector.detectAndDecode(variant)
        if points is not None and payload:
            return parse_wifi_qr(payload)
    return None


def scan_wifi_qr(camera_device, deadline, cancel_event, log=None):
    capture = cv2.VideoCapture(camera_device, cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError("USB webcam could not start QR scanning")
    if log is not None:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log(f"Wi-Fi QR scanner capture resolution: {width}x{height}")
    detector = cv2.QRCodeDetector()
    try:
        while not cancel_event.is_set() and time.monotonic() < deadline:
            received, frame = capture.read()
            if not received:
                continue
            try:
                credentials = decode_wifi_qr_frame(frame, detector)
            except ValueError:
                credentials = None
            if credentials is not None:
                return credentials
    finally:
        capture.release()
    return None


def connect_wifi(credentials, socket_path=SOCKET_PATH, timeout=70):
    request = json.dumps(credentials, ensure_ascii=False).encode("utf-8") + b"\n"
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
        raise RuntimeError("Wi-Fi helper returned an invalid response") from exc
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Wi-Fi connection failed"))
    return response.get("ssid", credentials["ssid"])