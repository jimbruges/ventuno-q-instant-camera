import argparse
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from npu_generate import DEFAULT_MODEL_DIR, StableDiffusionQnn


DEFAULT_EDITOR_MODEL_DIR = (
    Path.home() / "instant-camera-ai" / "models" / "qcs8275-instruct-pix2pix"
)
DEFAULT_ADAPTER_MODEL_DIR = (
    Path.home() / "instant-camera-ai" / "models" / "qcs8275-ip-adapter-plus"
)
EXPORTED_VAE_ENCODER_SCALE = 0.18215
CLIP_IMAGE_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_IMAGE_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


class EulerAncestralScheduler:
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

    def step(self, noise_prediction, step_index, sample, generator):
        sigma = float(self.sigmas[step_index])
        sigma_next = float(self.sigmas[step_index + 1])
        sigma_up = np.sqrt(
            max(0.0, sigma_next**2 * (sigma**2 - sigma_next**2) / sigma**2)
        )
        sigma_down = np.sqrt(max(0.0, sigma_next**2 - sigma_up**2))
        derivative = noise_prediction
        previous = sample + derivative * (sigma_down - sigma)
        if sigma_up:
            noise = generator.standard_normal(sample.shape, dtype=np.float32)
            previous += noise * sigma_up
        return previous.astype(np.float32, copy=False)


class InstructPix2PixQnn(StableDiffusionQnn):
    def __init__(self, base_model_dir=DEFAULT_MODEL_DIR, editor_model_dir=DEFAULT_EDITOR_MODEL_DIR):
        super().__init__(base_model_dir, components=("text_encoder", "vae"))
        editor_model_dir = Path(editor_model_dir)
        self.vae_encoder = self._load_float_session(
            editor_model_dir / "vae_encoder" / "model.onnx"
        )
        self.editor_unet = self._load_float_session(
            editor_model_dir / "editor_unet" / "model.onnx"
        )

    def _load_float_session(self, path):
        if not path.is_file():
            raise FileNotFoundError(f"QNN model is missing: {path}")
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.add_provider_for_devices(self.devices, self.provider_options)
        return ort.InferenceSession(str(path), sess_options=options)

    @staticmethod
    def _run_float(session, **inputs):
        feed = {
            session_input.name: inputs[session_input.name].astype(np.float32, copy=False)
            for session_input in session.get_inputs()
        }
        outputs = session.run(None, feed)
        return outputs[0] if len(outputs) == 1 else tuple(outputs)

    @staticmethod
    def _prepare_source(source):
        image = source.convert("RGB")
        original_size = image.size
        scale = min(512 / image.width, 512 / image.height)
        resized_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(resized_size, Image.Resampling.LANCZOS)
        pixels = np.asarray(image, dtype=np.float32)
        pad_left = (512 - resized_size[0]) // 2
        pad_top = (512 - resized_size[1]) // 2
        pixels = np.pad(
            pixels,
            (
                (pad_top, 512 - resized_size[1] - pad_top),
                (pad_left, 512 - resized_size[0] - pad_left),
                (0, 0),
            ),
            mode="edge",
        )
        tensor = np.transpose(pixels / 127.5 - 1.0, (2, 0, 1))[None]
        crop = (pad_left, pad_top, pad_left + resized_size[0], pad_top + resized_size[1])
        return tensor.astype(np.float32), crop, original_size

    def edit(
        self,
        source,
        prompt,
        steps=20,
        seed=47,
        guidance_scale=7.5,
        image_guidance_scale=1.5,
    ):
        started = time.perf_counter()
        source_tensor, crop, original_size = self._prepare_source(source)
        image_latents = self._run_float(self.vae_encoder, image=source_tensor)
        image_latents /= EXPORTED_VAE_ENCODER_SCALE
        zero_image_latents = np.zeros_like(image_latents)
        conditional, unconditional = self.encode_prompt(prompt)

        scheduler = EulerAncestralScheduler(steps)
        generator = np.random.default_rng(seed)
        latents = generator.standard_normal((1, 4, 64, 64), dtype=np.float32)
        latents *= scheduler.init_noise_sigma

        for step_index, timestep in enumerate(scheduler.timesteps):
            scaled_latents = scheduler.scale_model_input(latents, step_index)
            time_input = np.array([timestep], dtype=np.float32)
            noise_text = self._run_float(
                self.editor_unet,
                sample=np.concatenate((scaled_latents, image_latents), axis=1),
                timestep=time_input,
                text_embedding=conditional,
            )
            noise_image = self._run_float(
                self.editor_unet,
                sample=np.concatenate((scaled_latents, image_latents), axis=1),
                timestep=time_input,
                text_embedding=unconditional,
            )
            noise_unconditional = self._run_float(
                self.editor_unet,
                sample=np.concatenate((scaled_latents, zero_image_latents), axis=1),
                timestep=time_input,
                text_embedding=unconditional,
            )
            guided_noise = (
                noise_unconditional
                + guidance_scale * (noise_text - noise_image)
                + image_guidance_scale * (noise_image - noise_unconditional)
            )
            latents = scheduler.step(guided_noise, step_index, latents, generator)
            print(f"Edit step {step_index + 1}/{steps}", flush=True)

        image = self._run(self.vae, np.transpose(latents, (0, 2, 3, 1)))
        image = Image.fromarray(np.clip(image[0] * 255.0, 0, 255).astype(np.uint8))
        image = image.crop(crop).resize(original_size, Image.Resampling.LANCZOS)
        print(f"Edited in {time.perf_counter() - started:.2f}s", flush=True)
        return image


class InstructPix2PixIpAdapterQnn(InstructPix2PixQnn):
    def __init__(
        self,
        base_model_dir=DEFAULT_MODEL_DIR,
        editor_model_dir=DEFAULT_EDITOR_MODEL_DIR,
        adapter_model_dir=DEFAULT_ADAPTER_MODEL_DIR,
    ):
        StableDiffusionQnn.__init__(
            self, base_model_dir, components=("text_encoder", "vae")
        )
        editor_model_dir = Path(editor_model_dir)
        self.vae_encoder = self._load_float_session(
            editor_model_dir / "vae_encoder" / "model.onnx"
        )
        adapter_model_dir = Path(adapter_model_dir)
        self.reference_encoder = self._load_float_session(
            adapter_model_dir / "reference_encoder" / "model.onnx"
        )
        self.editor_unet = self._load_float_session(
            adapter_model_dir / "adapter_unet" / "model.onnx"
        )
        self.editor_unet_batch = self.editor_unet.get_inputs()[0].shape[0]
        if self.editor_unet_batch not in (1, 3):
            raise ValueError(
                f"Adapter U-Net batch must be 1 or 3, got {self.editor_unet_batch}"
            )
        self._negative_reference_embedding = None

    @staticmethod
    def _prepare_reference(reference):
        image = reference.convert("RGB")
        scale = 224 / min(image.size)
        resized_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(resized_size, Image.Resampling.BICUBIC)
        left = (image.width - 224) // 2
        top = (image.height - 224) // 2
        pixels = np.asarray(image.crop((left, top, left + 224, top + 224)), dtype=np.float32)
        pixels = (pixels / 255.0 - CLIP_IMAGE_MEAN) / CLIP_IMAGE_STD
        return np.transpose(pixels, (2, 0, 1))[None].astype(np.float32)

    def _reference_embeddings(self, reference):
        if self._negative_reference_embedding is None:
            self._negative_reference_embedding = self._run_float(
                self.reference_encoder,
                pixel_values=np.zeros((1, 3, 224, 224), dtype=np.float32),
            )[:, None]
        if reference is None:
            return self._negative_reference_embedding, self._negative_reference_embedding
        positive = self._run_float(
            self.reference_encoder,
            pixel_values=self._prepare_reference(reference),
        )[:, None]
        return positive, self._negative_reference_embedding

    def edit(
        self,
        source,
        prompt,
        steps=20,
        seed=47,
        guidance_scale=7.5,
        image_guidance_scale=1.5,
        reference=None,
    ):
        started = time.perf_counter()
        source_tensor, crop, original_size = self._prepare_source(source)
        image_latents = self._run_float(self.vae_encoder, image=source_tensor)
        image_latents /= EXPORTED_VAE_ENCODER_SCALE
        zero_image_latents = np.zeros_like(image_latents)
        conditional, unconditional = self.encode_prompt(prompt)
        positive_reference, negative_reference = self._reference_embeddings(reference)

        scheduler = EulerAncestralScheduler(steps)
        generator = np.random.default_rng(seed)
        latents = generator.standard_normal((1, 4, 64, 64), dtype=np.float32)
        latents *= scheduler.init_noise_sigma

        for step_index, timestep in enumerate(scheduler.timesteps):
            scaled_latents = scheduler.scale_model_input(latents, step_index)
            time_input = np.array([timestep], dtype=np.float32)
            if self.editor_unet_batch == 3:
                latent_with_image = np.concatenate(
                    (scaled_latents, image_latents), axis=1
                )
                noise = self._run_float(
                    self.editor_unet,
                    sample=np.concatenate(
                        (
                            latent_with_image,
                            latent_with_image,
                            np.concatenate(
                                (scaled_latents, zero_image_latents), axis=1
                            ),
                        )
                    ),
                    timestep=np.repeat(time_input, 3),
                    text_embedding=np.concatenate(
                        (conditional, unconditional, unconditional)
                    ),
                    reference_embedding=np.concatenate(
                        (
                            positive_reference,
                            negative_reference,
                            negative_reference,
                        )
                    ),
                )
                noise_text, noise_image, noise_unconditional = np.split(
                    noise, 3, axis=0
                )
            else:
                common = {"timestep": time_input}
                noise_text = self._run_float(
                    self.editor_unet,
                    sample=np.concatenate((scaled_latents, image_latents), axis=1),
                    text_embedding=conditional,
                    reference_embedding=positive_reference,
                    **common,
                )
                noise_image = self._run_float(
                    self.editor_unet,
                    sample=np.concatenate((scaled_latents, image_latents), axis=1),
                    text_embedding=unconditional,
                    reference_embedding=negative_reference,
                    **common,
                )
                noise_unconditional = self._run_float(
                    self.editor_unet,
                    sample=np.concatenate((scaled_latents, zero_image_latents), axis=1),
                    text_embedding=unconditional,
                    reference_embedding=negative_reference,
                    **common,
                )
            guided_noise = (
                noise_unconditional
                + guidance_scale * (noise_text - noise_image)
                + image_guidance_scale * (noise_image - noise_unconditional)
            )
            latents = scheduler.step(guided_noise, step_index, latents, generator)
            print(f"Adapter edit step {step_index + 1}/{steps}", flush=True)

        image = self._run(self.vae, np.transpose(latents, (0, 2, 3, 1)))
        image = Image.fromarray(np.clip(image[0] * 255.0, 0, 255).astype(np.uint8))
        image = image.crop(crop).resize(original_size, Image.Resampling.LANCZOS)
        print(f"Adapter edit completed in {time.perf_counter() - started:.2f}s", flush=True)
        return image


def main():
    parser = argparse.ArgumentParser(description="Edit an image on the VENTUNO Q NPU")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--editor-model-dir", type=Path, default=DEFAULT_EDITOR_MODEL_DIR)
    parser.add_argument("--adapter-model-dir", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--guidance-scale", type=float, default=7.5)
    parser.add_argument("--image-guidance-scale", type=float, default=1.5)
    args = parser.parse_args()

    if args.adapter_model_dir:
        pipeline = InstructPix2PixIpAdapterQnn(
            args.base_model_dir, args.editor_model_dir, args.adapter_model_dir
        )
    else:
        pipeline = InstructPix2PixQnn(args.base_model_dir, args.editor_model_dir)
    with Image.open(args.input) as source:
        edit_args = (
            source,
            args.prompt,
            args.steps,
            args.seed,
            args.guidance_scale,
            args.image_guidance_scale,
        )
        if args.adapter_model_dir:
            if args.reference:
                with Image.open(args.reference) as reference:
                    image = pipeline.edit(
                        *edit_args, reference=reference
                    )
            else:
                image = pipeline.edit(*edit_args)
        else:
            image = pipeline.edit(*edit_args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)


if __name__ == "__main__":
    main()