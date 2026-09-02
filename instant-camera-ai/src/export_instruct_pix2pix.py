import argparse
import json
from pathlib import Path

import qai_hub as hub
import torch
from diffusers import AutoencoderKL, UNet2DConditionModel
from huggingface_hub import snapshot_download


MODEL_ID = "timbrooks/instruct-pix2pix"
TARGET_DEVICE = "Arduino VENTUNO Q"
DEFAULT_MODEL_DIR = Path.home() / "instant-camera-ai" / "models" / "instruct-pix2pix"
DEFAULT_BUILD_DIR = Path.home() / "instant-camera-ai" / "build" / "instruct-pix2pix"


class EditorUnet(torch.nn.Module):
    def __init__(self, unet):
        super().__init__()
        self.unet = unet
        self.compute_dtype = next(unet.parameters()).dtype

    def forward(self, sample, timestep, text_embedding):
        output = self.unet(
            sample.to(self.compute_dtype),
            timestep.to(self.compute_dtype),
            encoder_hidden_states=text_embedding.to(self.compute_dtype),
            return_dict=False,
        )[0]
        return output.float()


class VaeEncoder(torch.nn.Module):
    def __init__(self, vae):
        super().__init__()
        self.encoder = vae.encoder
        self.quant_conv = vae.quant_conv
        self.compute_dtype = next(vae.parameters()).dtype

    def forward(self, image):
        moments = self.quant_conv(self.encoder(image.to(self.compute_dtype)))
        mean, _ = torch.chunk(moments, 2, dim=1)
        return mean.float()


class VaeDecoder(torch.nn.Module):
    def __init__(self, vae):
        super().__init__()
        self.post_quant_conv = vae.post_quant_conv
        self.decoder = vae.decoder
        self.scaling_factor = vae.config.scaling_factor
        self.compute_dtype = next(vae.parameters()).dtype

    def forward(self, latent):
        latent = latent.to(self.compute_dtype) / self.scaling_factor
        image = self.decoder(self.post_quant_conv(latent))
        return (image / 2 + 0.5).clamp(0, 1).float()


def download_model(model_dir):
    snapshot_download(
        MODEL_ID,
        local_dir=model_dir,
        allow_patterns=[
            "model_index.json",
            "scheduler/*",
            "tokenizer/*",
            "text_encoder/config.json",
            "text_encoder/model.fp16.safetensors",
            "unet/config.json",
            "unet/diffusion_pytorch_model.fp16.safetensors",
            "vae/config.json",
            "vae/diffusion_pytorch_model.fp16.safetensors",
        ],
    )


def export_component(module, inputs, output_path, input_names, output_name):
    module.eval()
    with torch.no_grad():
        torch.onnx.export(
            module,
            inputs,
            str(output_path),
            input_names=input_names,
            output_names=[output_name],
            opset_version=20,
            do_constant_folding=False,
            dynamo=False,
            external_data=True,
        )


def prepare(model_dir, build_dir):
    download_model(model_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    dtype = torch.float32

    unet = UNet2DConditionModel.from_pretrained(
        model_dir, subfolder="unet", variant="fp16", torch_dtype=torch.float16
    ).to(dtype)
    export_component(
        EditorUnet(unet),
        (
            torch.zeros(1, 8, 64, 64),
            torch.zeros(1),
            torch.zeros(1, 77, 768),
        ),
        build_dir / "editor_unet.onnx",
        ["sample", "timestep", "text_embedding"],
        "noise_prediction",
    )
    del unet

    vae = AutoencoderKL.from_pretrained(
        model_dir, subfolder="vae", variant="fp16", torch_dtype=torch.float16
    ).to(dtype)
    export_component(
        VaeEncoder(vae),
        (torch.zeros(1, 3, 512, 512),),
        build_dir / "vae_encoder.onnx",
        ["image"],
        "latent",
    )
    export_component(
        VaeDecoder(vae),
        (torch.zeros(1, 4, 64, 64),),
        build_dir / "vae_decoder.onnx",
        ["latent"],
        "image",
    )


def submit(build_dir):
    client = hub.Client()
    device = hub.Device(TARGET_DEVICE)
    options = "--target_runtime qnn_context_binary --qnn_options default_graph_htp_precision=FLOAT16"
    specs = {
        "editor_unet": {
            "sample": ((1, 8, 64, 64), "float32"),
            "timestep": ((1,), "float32"),
            "text_embedding": ((1, 77, 768), "float32"),
        },
        "vae_encoder": {"image": ((1, 3, 512, 512), "float32")},
        "vae_decoder": {"latent": ((1, 4, 64, 64), "float32")},
    }
    jobs = {}
    for component, input_specs in specs.items():
        job = client.submit_compile_job(
            model=build_dir / f"{component}.onnx",
            device=device,
            name=f"instruct-pix2pix-{component}-fp16",
            input_specs=input_specs,
            options=options,
        )
        jobs[component] = job.job_id
        print(f"{component}: {job.job_id}", flush=True)
    (build_dir / "compile_jobs.json").write_text(json.dumps(jobs, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "submit", "all"))
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    args = parser.parse_args()
    if args.command in ("prepare", "all"):
        prepare(args.model_dir, args.build_dir)
    if args.command in ("submit", "all"):
        submit(args.build_dir)


if __name__ == "__main__":
    main()