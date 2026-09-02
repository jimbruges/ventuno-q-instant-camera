import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from npu_generate import EulerScheduler, StableDiffusionQnn


DEFAULT_MODEL_DIR = (
    Path.home()
    / "instant-camera-ai"
    / "models"
    / "qcs8275-controlnet-canny"
    / "controlnet_canny-precompiled_qnn_onnx-w8a16-qualcomm_qcs8275"
)
DUCK_GUIDE_PATH = Path(__file__).resolve().parent.parent / "assets" / "duck-guide.png"


def edge_map(image, threshold=45):
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    gradient_x = np.zeros_like(gray)
    gradient_y = np.zeros_like(gray)
    gradient_x[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gradient_y[1:-1, :] = gray[2:, :] - gray[:-2, :]
    return np.hypot(gradient_x, gradient_y) >= threshold


def model_canvas(image):
    original = image.convert("RGB")
    fitted = ImageOps.contain(original, (512, 512), Image.Resampling.LANCZOS)
    square = ImageOps.fit(original, (512, 512), Image.Resampling.LANCZOS)
    square = square.filter(ImageFilter.GaussianBlur(16))
    content_left = (512 - fitted.width) // 2
    content_top = (512 - fitted.height) // 2
    square.paste(fitted, (content_left, content_top))
    content_box = (
        content_left,
        content_top,
        content_left + fitted.width,
        content_top + fitted.height,
    )
    return original, square, content_box


def full_frame_guide(image):
    original, square, content_box = model_canvas(image)
    guide = Image.fromarray((edge_map(square) * 255).astype(np.uint8), mode="L")
    condition = np.asarray(guide.convert("RGB"), dtype=np.float32)[None] / 255.0
    return square, condition, None, content_box, original.size


def control_guide(image):
    original, square, content_box = model_canvas(image)
    fitted_width = content_box[2] - content_box[0]
    fitted_height = content_box[3] - content_box[1]
    edges = edge_map(square)

    region_width = min(150, fitted_width // 3)
    region_height = min(170, fitted_height // 2)
    candidate_top = content_box[3] - region_height - 18
    candidates = [
        (
            content_box[0] + 24,
            candidate_top,
            content_box[0] + 24 + region_width,
            candidate_top + region_height,
        ),
        (
            content_box[0] + (fitted_width - region_width) // 2,
            candidate_top,
            content_box[0] + (fitted_width + region_width) // 2,
            candidate_top + region_height,
        ),
        (
            content_box[2] - region_width - 24,
            candidate_top,
            content_box[2] - 24,
            candidate_top + region_height,
        ),
    ]
    left, top, right, bottom = min(
        candidates, key=lambda box: int(edges[box[1]:box[3], box[0]:box[2]].sum())
    )

    with Image.open(DUCK_GUIDE_PATH) as template_image:
        template = template_image.convert("RGB")
    hsv = np.asarray(template.convert("HSV"))
    colored = (hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 80)
    rows, columns = np.nonzero(colored)
    if not len(rows):
        raise RuntimeError("Duck guide does not contain a colored subject")
    template = template.crop(
        (columns.min(), rows.min(), columns.max() + 1, rows.max() + 1)
    )
    template = ImageOps.contain(
        template, (region_width, region_height), Image.Resampling.LANCZOS
    )
    duck_edges = edge_map(template, threshold=32)
    duck_left = left + (region_width - template.width) // 2
    duck_top = top + (region_height - template.height) // 2
    edges[
        duck_top:duck_top + template.height,
        duck_left:duck_left + template.width,
    ] |= duck_edges
    guide = Image.fromarray((edges * 255).astype(np.uint8), mode="L")

    blend_mask = Image.new("L", square.size)
    mask_draw = ImageDraw.Draw(blend_mask)
    mask_draw.ellipse(
        (
            max(content_box[0], left - 24),
            max(content_box[1], top - 20),
            min(content_box[2], right + 35),
            min(content_box[3], bottom + 28),
        ),
        fill=255,
    )
    blend_mask = blend_mask.filter(ImageFilter.GaussianBlur(24))
    condition = np.asarray(guide.convert("RGB"), dtype=np.float32)[None] / 255.0
    return square, condition, blend_mask, content_box, original.size


class ControlNetQnn(StableDiffusionQnn):
    def __init__(self, model_dir):
        super().__init__(model_dir)
        self.controlnet = self._load("controlnet")

    def generate(
        self,
        prompt,
        control_image,
        steps=20,
        seed=47,
        guidance_scale=7.5,
        control_strength=0.45,
        full_frame=False,
    ):
        started = time.perf_counter()
        conditional, unconditional = self.encode_prompt(prompt)
        guide_builder = full_frame_guide if full_frame else control_guide
        source, image_condition, blend_mask, content_box, output_size = guide_builder(
            control_image
        )
        scheduler = EulerScheduler(steps)
        latents = np.random.default_rng(seed).standard_normal(
            (1, 4, 64, 64), dtype=np.float32
        )
        latents *= scheduler.init_noise_sigma

        for step_index, timestep in enumerate(scheduler.timesteps):
            latent_input = scheduler.scale_model_input(latents, step_index)
            latent_input = np.transpose(latent_input, (0, 2, 3, 1))
            time_input = np.array([[timestep]], dtype=np.float32)
            residuals = self._run(
                self.controlnet,
                latent_input,
                time_input,
                conditional,
                image_condition,
            )
            if not isinstance(residuals, tuple):
                raise RuntimeError("ControlNet did not return residual tensors")
            residuals = tuple(residual * control_strength for residual in residuals)
            noise_conditional = self._run(
                self.unet, latent_input, time_input, conditional, *residuals
            )
            noise_unconditional = self._run(
                self.unet, latent_input, time_input, unconditional, *residuals
            )
            noise = noise_unconditional + guidance_scale * (
                noise_conditional - noise_unconditional
            )
            latents = scheduler.step(
                np.transpose(noise, (0, 3, 1, 2)), step_index, latents
            )
            print(f"Step {step_index + 1}/{steps}", flush=True)

        image = self._run(self.vae, np.transpose(latents, (0, 2, 3, 1)))
        print(f"Generated in {time.perf_counter() - started:.2f}s", flush=True)
        generated = Image.fromarray(
            np.clip(image[0] * 255.0, 0, 255).astype(np.uint8)
        )
        result = generated if blend_mask is None else Image.composite(
            generated, source, blend_mask
        )
        result = result.crop(content_box)
        return result.resize(output_size, Image.Resampling.LANCZOS)


def main():
    parser = argparse.ArgumentParser(description="Generate a guided image on QCS8275 NPU")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--guidance-scale", type=float, default=7.5)
    parser.add_argument("--control-strength", type=float, default=0.45)
    parser.add_argument("--full-frame", action="store_true")
    args = parser.parse_args()

    pipeline = ControlNetQnn(args.model_dir)
    with Image.open(args.image) as image:
        generated = pipeline.generate(
            args.prompt,
            image.convert("RGB"),
            args.steps,
            args.seed,
            args.guidance_scale,
            args.control_strength,
            args.full_frame,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    generated.save(args.output)


if __name__ == "__main__":
    main()