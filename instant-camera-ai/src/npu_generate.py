import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
import onnxruntime_qnn as qnn
from PIL import Image
from transformers import CLIPTokenizer


PROJECT_DIR = Path(
    os.getenv("INSTANT_CAMERA_AI_HOME", Path.home() / "instant-camera-ai")
)
DEFAULT_MODEL_DIR = (
    PROJECT_DIR
    / "models"
    / "qcs8275-sd15"
    / "stable_diffusion_v1_5-precompiled_qnn_onnx-w8a16-qualcomm_qcs8275"
)
TOKENIZER_REPO = "stable-diffusion-v1-5/stable-diffusion-v1-5"
LOCAL_TOKENIZER_DIR = PROJECT_DIR / "models" / "tokenizer"


@dataclass
class QuantizedModel:
    session: ort.InferenceSession
    inputs: dict[str, dict]
    outputs: dict[str, dict]


class EulerScheduler:
    def __init__(self, steps):
        betas = np.linspace(0.00085**0.5, 0.012**0.5, 1000) ** 2
        alphas_cumprod = np.cumprod(1.0 - betas)
        training_sigmas = np.sqrt((1.0 - alphas_cumprod) / alphas_cumprod)
        self.timesteps = np.linspace(0, 999, steps, dtype=np.float32)[::-1].copy()
        sigmas = np.interp(self.timesteps, np.arange(1000), training_sigmas)
        self.sigmas = np.append(sigmas, 0.0).astype(np.float32)

    @property
    def init_noise_sigma(self):
        return float(self.sigmas.max())

    def scale_model_input(self, sample, step_index):
        sigma = float(self.sigmas[step_index])
        return sample / np.sqrt(sigma * sigma + 1.0)

    def step(self, noise_prediction, step_index, sample):
        sigma = float(self.sigmas[step_index])
        sigma_next = float(self.sigmas[step_index + 1])
        predicted_original = sample - sigma * noise_prediction
        derivative = (sample - predicted_original) / sigma
        return sample + derivative * (sigma_next - sigma)


def quantize(array, encoding, dtype):
    scale = encoding["quantization_parameters"]["scale"]
    zero_point = encoding["quantization_parameters"]["zero_point"]
    quantized = np.rint(array.astype(np.float64) / scale) + zero_point
    limits = np.iinfo(dtype)
    return np.clip(quantized, limits.min, limits.max).astype(dtype)


def dequantize(array, encoding):
    scale = encoding["quantization_parameters"]["scale"]
    zero_point = encoding["quantization_parameters"]["zero_point"]
    return ((array.astype(np.int32) - zero_point) * scale).astype(np.float32)


class StableDiffusionQnn:
    def __init__(self, model_dir, components=("text_encoder", "unet", "vae")):
        self.model_dir = Path(model_dir)
        metadata = json.loads((self.model_dir / "metadata.json").read_text())
        self.model_metadata = metadata["model_files"]
        chipset = metadata["chipset_attributes"]

        ort.register_execution_provider_library(qnn.EP_NAME, qnn.get_library_path())
        devices = [device for device in ort.get_ep_devices() if device.ep_name == qnn.EP_NAME]
        if not devices:
            raise RuntimeError("QNNExecutionProvider did not expose a device")

        qnn_dir = Path(qnn.get_library_path()).parent
        self.provider_options = {
            "backend_path": str(qnn_dir / "libQnnHtp.so"),
            "soc_model": str(chipset["soc_model"]),
            "htp_arch": str(chipset["htp_version"]),
            "device_id": "0",
            "htp_performance_mode": "burst",
        }
        self.devices = devices

        started = time.perf_counter()
        for component in components:
            setattr(self, component, self._load(component))
        if not LOCAL_TOKENIZER_DIR.is_dir():
            raise FileNotFoundError(
                f"Local tokenizer is missing at {LOCAL_TOKENIZER_DIR}; "
                "run scripts/install-models.sh from the source repository"
            )
        self.tokenizer = CLIPTokenizer.from_pretrained(LOCAL_TOKENIZER_DIR)
        print(f"Models ready in {time.perf_counter() - started:.2f}s", flush=True)

    def _load(self, name):
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.add_provider_for_devices(self.devices, self.provider_options)
        session = ort.InferenceSession(
            str(self.model_dir / f"{name}.onnx"), sess_options=options
        )
        metadata = self.model_metadata[f"{name}.onnx"]
        return QuantizedModel(session, metadata["inputs"], metadata["outputs"])

    @staticmethod
    def _dtype(session_input):
        dtypes = {
            "tensor(int32)": np.int32,
            "tensor(uint16)": np.uint16,
            "tensor(float)": np.float32,
        }
        return dtypes[session_input.type]

    def _run(self, model, *arrays):
        feed = {}
        for session_input, array in zip(model.session.get_inputs(), arrays, strict=True):
            encoding = model.inputs[session_input.name]
            dtype = self._dtype(session_input)
            if "quantization_parameters" in encoding:
                feed[session_input.name] = quantize(array, encoding, dtype)
            else:
                feed[session_input.name] = array.astype(dtype, copy=False)

        outputs = model.session.run(None, feed)
        decoded = []
        for session_output, array in zip(
            model.session.get_outputs(), outputs, strict=True
        ):
            encoding = model.outputs[session_output.name]
            if "quantization_parameters" in encoding:
                array = dequantize(array, encoding)
            decoded.append(array)
        return decoded[0] if len(decoded) == 1 else tuple(decoded)

    def encode_prompt(self, prompt):
        tokens = self.tokenizer(
            prompt,
            padding="max_length",
            max_length=self.tokenizer.model_max_length,
            truncation=True,
            return_tensors="np",
        ).input_ids.astype(np.int32)
        empty_tokens = self.tokenizer(
            "",
            padding="max_length",
            max_length=self.tokenizer.model_max_length,
            return_tensors="np",
        ).input_ids.astype(np.int32)
        return self._run(self.text_encoder, tokens), self._run(
            self.text_encoder, empty_tokens
        )

    def generate(self, prompt, steps=20, seed=47, guidance_scale=7.5):
        started = time.perf_counter()
        conditional, unconditional = self.encode_prompt(prompt)
        scheduler = EulerScheduler(steps)
        latents = np.random.default_rng(seed).standard_normal(
            (1, 4, 64, 64), dtype=np.float32
        )
        latents *= scheduler.init_noise_sigma

        for step_index, timestep in enumerate(scheduler.timesteps):
            latent_input = scheduler.scale_model_input(latents, step_index)
            latent_input = np.transpose(latent_input, (0, 2, 3, 1))
            time_input = np.array([[timestep]], dtype=np.float32)
            noise_conditional = self._run(
                self.unet, latent_input, time_input, conditional
            )
            noise_unconditional = self._run(
                self.unet, latent_input, time_input, unconditional
            )
            noise = noise_unconditional + guidance_scale * (
                noise_conditional - noise_unconditional
            )
            noise = np.transpose(noise, (0, 3, 1, 2))
            latents = scheduler.step(noise, step_index, latents)
            print(f"Step {step_index + 1}/{steps}", flush=True)

        image = self._run(self.vae, np.transpose(latents, (0, 2, 3, 1)))
        print(f"Generated in {time.perf_counter() - started:.2f}s", flush=True)
        return Image.fromarray(np.clip(image[0] * 255.0, 0, 255).astype(np.uint8))


def main():
    parser = argparse.ArgumentParser(description="Generate an image on QCS8275 NPU")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--guidance-scale", type=float, default=7.5)
    args = parser.parse_args()

    pipeline = StableDiffusionQnn(args.model_dir)
    image = pipeline.generate(
        args.prompt, args.steps, args.seed, args.guidance_scale
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)


if __name__ == "__main__":
    main()