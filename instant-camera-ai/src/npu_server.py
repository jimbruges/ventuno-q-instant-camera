import argparse
import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from instruct_pix2pix_generate import (
    DEFAULT_EDITOR_MODEL_DIR,
    InstructPix2PixIpAdapterQnn,
    InstructPix2PixQnn,
)
from npu_generate import DEFAULT_MODEL_DIR, StableDiffusionQnn


class NpuServer(ThreadingHTTPServer):
    def __init__(self, address, pipeline):
        super().__init__(address, NpuRequestHandler)
        self.pipeline = pipeline
        self.generation_lock = threading.Lock()


class NpuRequestHandler(BaseHTTPRequestHandler):
    server: NpuServer

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._send_json({"status": "ready"})

    def do_POST(self):
        if self.path not in ("/generate", "/edit"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 32 * 1024 * 1024:
                raise ValueError("request is too large")
            request = json.loads(self.rfile.read(length))
            prompt = request.get("prompt", "").strip()
            if not prompt:
                raise ValueError("prompt is required")
            steps = int(request.get("steps", 20))
            seed = int(request.get("seed", 47))
            guidance_scale = float(request.get("guidance_scale", 7.5))
            image_guidance_scale = float(request.get("image_guidance_scale", 1.5))
            if not 4 <= steps <= 30:
                raise ValueError("steps must be between 4 and 30")
            if not 0 <= seed <= 0x7FFFFFFF:
                raise ValueError("seed must be between 0 and 2147483647")
            if not 1.0 <= guidance_scale <= 15.0:
                raise ValueError("guidance_scale must be between 1 and 15")
            if not 1.0 <= image_guidance_scale <= 3.0:
                raise ValueError("image_guidance_scale must be between 1 and 3")
            with self.server.generation_lock:
                if self.path == "/edit":
                    encoded_image = request.get("image", "")
                    if not encoded_image:
                        raise ValueError("image is required")
                    source_bytes = base64.b64decode(encoded_image, validate=True)
                    with Image.open(io.BytesIO(source_bytes)) as source:
                        edit_args = (
                            source,
                            prompt,
                            steps,
                            seed,
                            guidance_scale,
                            image_guidance_scale,
                        )
                        if isinstance(self.server.pipeline, InstructPix2PixIpAdapterQnn):
                            encoded_reference = request.get("reference_image", "")
                            if encoded_reference:
                                reference_bytes = base64.b64decode(
                                    encoded_reference, validate=True
                                )
                                with Image.open(io.BytesIO(reference_bytes)) as reference:
                                    image = self.server.pipeline.edit(
                                        *edit_args,
                                        reference=reference,
                                    )
                            else:
                                image = self.server.pipeline.edit(*edit_args)
                        else:
                            image = self.server.pipeline.edit(*edit_args)
                else:
                    image = self.server.pipeline.generate(
                        prompt,
                        steps,
                        seed,
                        guidance_scale,
                    )
            output = io.BytesIO()
            image.save(output, format="PNG")
            body = output.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
        print(f"NPU API: {format % args}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Persistent VENTUNO Q NPU image service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7100)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--pipeline", choices=("generate", "edit"), default="generate")
    parser.add_argument("--editor-model-dir", default=DEFAULT_EDITOR_MODEL_DIR)
    parser.add_argument("--adapter-model-dir")
    args = parser.parse_args()

    if args.pipeline == "edit":
        if args.adapter_model_dir:
            pipeline = InstructPix2PixIpAdapterQnn(
                args.model_dir, args.editor_model_dir, args.adapter_model_dir
            )
        else:
            pipeline = InstructPix2PixQnn(args.model_dir, args.editor_model_dir)
    else:
        pipeline = StableDiffusionQnn(args.model_dir)
    server = NpuServer((args.host, args.port), pipeline)
    print(f"NPU API ready at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()