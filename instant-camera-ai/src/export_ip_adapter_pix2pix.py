import argparse
import json
from pathlib import Path

import onnx
import qai_hub as hub
import torch
from diffusers import (
    AutoencoderKL,
    EulerAncestralDiscreteScheduler,
    StableDiffusionInstructPix2PixPipeline,
    UNet2DConditionModel,
)
from transformers import CLIPTextModel, CLIPTokenizer


MODEL_DIR = Path.home() / "instant-camera-ai" / "models" / "instruct-pix2pix"
ADAPTER_DIR = Path.home() / "instant-camera-ai" / "models" / "ip-adapter-plus-sd15"
BUILD_DIR = Path.home() / "instant-camera-ai" / "build" / "ip-adapter-pix2pix"
BATCHED_BUILD_DIR = (
    Path.home() / "instant-camera-ai" / "build" / "ip-adapter-pix2pix-batched"
)
TARGET_DEVICE = "Arduino VENTUNO Q"


class ReferenceEncoder(torch.nn.Module):
    def __init__(self, image_encoder):
        super().__init__()
        self.image_encoder = image_encoder
        self.compute_dtype = next(image_encoder.parameters()).dtype

    def forward(self, pixel_values):
        return self.image_encoder(
            pixel_values.to(self.compute_dtype),
            output_hidden_states=True,
            return_dict=False,
        )[2][-2].float()


class AdapterEditorUnet(torch.nn.Module):
    def __init__(self, unet):
        super().__init__()
        self.unet = unet
        self.compute_dtype = next(unet.parameters()).dtype

    def forward(self, sample, timestep, text_embedding, reference_embedding):
        return self.unet(
            sample.to(self.compute_dtype),
            timestep.to(self.compute_dtype),
            encoder_hidden_states=text_embedding.to(self.compute_dtype),
            added_cond_kwargs={
                "image_embeds": [reference_embedding.to(self.compute_dtype)]
            },
            return_dict=False,
        )[0].float()


def load_pipeline():
    pipe = StableDiffusionInstructPix2PixPipeline(
        vae=AutoencoderKL.from_pretrained(
            MODEL_DIR, subfolder="vae", variant="fp16", torch_dtype=torch.float16
        ),
        text_encoder=CLIPTextModel.from_pretrained(
            MODEL_DIR,
            subfolder="text_encoder",
            variant="fp16",
            torch_dtype=torch.float16,
        ),
        tokenizer=CLIPTokenizer.from_pretrained(MODEL_DIR, subfolder="tokenizer"),
        unet=UNet2DConditionModel.from_pretrained(
            MODEL_DIR, subfolder="unet", variant="fp16", torch_dtype=torch.float16
        ),
        scheduler=EulerAncestralDiscreteScheduler.from_pretrained(
            MODEL_DIR, subfolder="scheduler"
        ),
        safety_checker=None,
        feature_extractor=None,
        image_encoder=None,
        requires_safety_checker=False,
    )
    pipe.load_ip_adapter(
        ADAPTER_DIR,
        subfolder="models",
        weight_name="ip-adapter-plus_sd15.safetensors",
        image_encoder_folder="image_encoder",
        low_cpu_mem_usage=True,
    )
    pipe.set_ip_adapter_scale(0.8)
    return pipe


def export_model(module, inputs, output, input_names, output_name):
    module.eval()
    output.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(
            module,
            inputs,
            str(output),
            input_names=input_names,
            output_names=[output_name],
            opset_version=20,
            do_constant_folding=False,
            dynamo=False,
            external_data=True,
        )


def consolidate_external_data(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    model = onnx.load(source, load_external_data=True)
    graph_path = destination / "model.onnx"
    onnx.save_model(
        model,
        graph_path,
        save_as_external_data=True,
        all_tensors_to_one_file=True,
        location="model.data",
        size_threshold=0,
    )
    onnx.checker.check_model(str(graph_path))


def prepare_reference():
    pipe = load_pipeline()
    export_model(
        ReferenceEncoder(pipe.image_encoder),
        (torch.zeros(1, 3, 224, 224),),
        BUILD_DIR / "reference_encoder.onnx",
        ["pixel_values"],
        "reference_embedding",
    )


def prepare_unet(batch_size=1, build_dir=BUILD_DIR):
    pipe = load_pipeline()
    export_model(
        AdapterEditorUnet(pipe.unet),
        (
            torch.zeros(batch_size, 8, 64, 64),
            torch.zeros(batch_size),
            torch.zeros(batch_size, 77, 768),
            torch.zeros(batch_size, 1, 257, 1280),
        ),
        build_dir / "adapter_unet.onnx",
        [
            "sample",
            "timestep",
            "text_embedding",
            "reference_embedding",
        ],
        "noise_prediction",
    )
    consolidate_external_data(
        build_dir / "adapter_unet.onnx", build_dir / "adapter_unet_source.onnx"
    )


def prepare_batched_unet():
    prepare_unet(batch_size=3, build_dir=BATCHED_BUILD_DIR)


def prepare():
    prepare_reference()
    prepare_unet()


def submit(components=("reference_encoder", "adapter_unet"), build_dir=BUILD_DIR):
    client = hub.Client()
    device = hub.Device(TARGET_DEVICE)
    options = "--target_runtime qnn_context_binary --qnn_options default_graph_htp_precision=FLOAT16"
    jobs_path = build_dir / "compile_jobs.json"
    jobs = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
    for name, model in (
        ("reference_encoder", build_dir / "reference_encoder.onnx"),
        ("adapter_unet", build_dir / "adapter_unet_source.onnx"),
    ):
        if name not in components:
            continue
        job = client.submit_compile_job(
            model=model,
            device=device,
            name=f"instruct-pix2pix-ip-adapter-plus-{name}-batch3-fp16"
            if build_dir == BATCHED_BUILD_DIR
            else f"instruct-pix2pix-ip-adapter-plus-{name}-fp16",
            options=options,
        )
        jobs[name] = job.job_id
        print(f"{name}: {job.job_id}", flush=True)
    jobs_path.write_text(json.dumps(jobs, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "prepare-reference",
            "prepare-unet",
            "prepare-batched-unet",
            "submit",
            "submit-unet",
            "submit-batched-unet",
        ),
    )
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "prepare-reference":
        prepare_reference()
    elif args.command == "prepare-unet":
        prepare_unet()
    elif args.command == "prepare-batched-unet":
        prepare_batched_unet()
    elif args.command == "submit":
        submit()
    elif args.command == "submit-unet":
        submit(("adapter_unet",))
    elif args.command == "submit-batched-unet":
        submit(("adapter_unet",), BATCHED_BUILD_DIR)


if __name__ == "__main__":
    main()