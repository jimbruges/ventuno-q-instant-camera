import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


GENIE_COMPOSE_PROJECT = "instant-me-camera"
GENIE_SERVICE = "genie-models-runner"


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
    )
    names = result.stdout.split()
    if len(names) != 1:
        raise RuntimeError(f"expected one Genie container, found {len(names)}")
    return names[0]


def set_genie_running(running):
    arguments = ["docker", "start" if running else "stop"]
    if not running:
        arguments.extend(["--time", "10"])
    subprocess.run(
        [*arguments, genie_container_name()],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if running:
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


class ModelWorker:
    def __init__(self, variants, default_variant, worker_port):
        self.variants = variants
        self.active_variant = None
        self.process = None
        self.worker_port = worker_port
        self.select(default_variant)

    def select(self, variant):
        if variant not in self.variants:
            choices = ", ".join(sorted(self.variants))
            raise ValueError(f"unknown model variant {variant!r}; choose from: {choices}")
        if variant == self.active_variant and self.process.poll() is None:
            return
        self.stop()
        print(f"Starting NPU model worker: {variant}", flush=True)
        self.process = subprocess.Popen([
            sys.executable,
            str(Path(__file__).with_name("npu_server.py")),
            "--pipeline", "edit",
            "--adapter-model-dir", self.variants[variant],
            "--host", "127.0.0.1",
            "--port", str(self.worker_port),
        ])
        deadline = time.monotonic() + 90
        health_url = f"http://127.0.0.1:{self.worker_port}/health"
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"NPU model worker exited with code {self.process.returncode}")
            try:
                with urllib.request.urlopen(health_url, timeout=1) as response:
                    if response.status == 200:
                        self.active_variant = variant
                        print(f"NPU model worker ready: {variant}", flush=True)
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        self.stop()
        raise TimeoutError(f"NPU model worker did not start: {variant}")

    def stop(self):
        self.active_variant = None
        if not self.process or self.process.poll() is not None:
            self.process = None
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process = None


class RouterServer(ThreadingHTTPServer):
    def __init__(self, address, worker):
        super().__init__(address, RouterHandler)
        self.worker = worker
        self.model_lock = threading.Lock()


class RouterHandler(BaseHTTPRequestHandler):
    server: RouterServer

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._send_json({
            "status": "ready",
            "model": self.server.worker.active_variant,
            "models": sorted(self.server.worker.variants),
        })

    def do_POST(self):
        if self.path == "/mode":
            self._set_mode()
            return
        if self.path != "/edit":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 32 * 1024 * 1024:
                raise ValueError("request is too large")
            body = self.rfile.read(length)
            request_data = json.loads(body)
            model = str(request_data.get("model", "")).strip()
            with self.server.model_lock:
                self.server.worker.select(model or self.server.worker.active_variant)
                request = urllib.request.Request(
                    f"http://127.0.0.1:{self.server.worker.worker_port}/edit",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=900) as response:
                    response_body = response.read()
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.headers.get_content_type())
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
        except urllib.error.HTTPError as exc:
            self._send_json(
                {"error": exc.read().decode("utf-8", errors="replace")},
                status=exc.code,
            )
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _set_mode(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request_data = json.loads(self.rfile.read(length))
            mode = request_data.get("mode")
            with self.server.model_lock:
                if mode == "describe":
                    self.server.worker.stop()
                    set_genie_running(True)
                elif mode == "local":
                    set_genie_running(False)
                    self.server.worker.select(request_data.get("model") or "standard")
                else:
                    raise ValueError("mode must be 'describe' or 'local'")
            self._send_json({"status": "ready", "mode": mode})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _send_json(self, value, status=200):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"NPU router: {format % args}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="VENTUNO Q NPU model router")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7100)
    parser.add_argument("--worker-port", type=int, default=7101)
    parser.add_argument(
        "--variant",
        action="append",
        required=True,
        metavar="NAME=PATH",
    )
    parser.add_argument("--default-variant", required=True)
    args = parser.parse_args()
    variants = {}
    for definition in args.variant:
        name, separator, path = definition.partition("=")
        if not separator or not name or not path:
            parser.error("--variant must use NAME=PATH")
        variants[name] = path
    if args.default_variant not in variants:
        parser.error("--default-variant must name a configured variant")
    try:
        set_genie_running(False)
    except Exception as exc:
        print(f"Genie was not released during startup: {exc}", flush=True)
    worker = ModelWorker(variants, args.default_variant, args.worker_port)
    server = RouterServer((args.host, args.port), worker)
    print(f"NPU router ready at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        worker.stop()


if __name__ == "__main__":
    main()