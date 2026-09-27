FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    COMFY_DIR=/workspace/ComfyUI \
    H3_COMFY_PORT=8188 \
    HF_HOME=/runpod-volume/hf-cache \
    H3_MODEL_ROOT=/runpod-volume/h3-models

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git curl ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
RUN git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git

WORKDIR /workspace/ComfyUI
RUN python3 -m pip install --break-system-packages --no-cache-dir \
    -r requirements.txt runpod "huggingface_hub[cli]"

# IMPORTANT: H3 weights are intentionally NOT baked into this image.
# RunPod's 30-minute managed build timeout was being hit while exporting a ~44 GB image.
# handler.py downloads the six files once into /runpod-volume/h3-models and symlinks
# them into ComfyUI. With a Serverless Network Volume, later workers reuse them.
COPY handler.py /workspace/handler.py
WORKDIR /workspace
CMD ["python3","-u","/workspace/handler.py"]
