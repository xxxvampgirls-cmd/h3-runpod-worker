FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    COMFY_DIR=/workspace/ComfyUI \
    H3_COMFY_PORT=8188 \
    HF_HOME=/runpod-volume/huggingface-cache \
    H3_MODEL_ROOT=/workspace/h3-runtime-models

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git curl ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
RUN git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git

WORKDIR /workspace/ComfyUI
RUN python3 -m pip install --break-system-packages --no-cache-dir \
    -r requirements.txt runpod "huggingface_hub[cli]" \
    && python3 -m pip install --break-system-packages --no-cache-dir --force-reinstall \
       --index-url https://download.pytorch.org/whl/cu128 \
       torch torchvision torchaudio

# H3 base weights are supplied by RunPod's Serverless Hugging Face Model Cache.
# Only the small Turbo LoRAs are fetched by the worker when needed; no paid Network Volume is required.
COPY handler.py /workspace/handler.py
WORKDIR /workspace
CMD ["python3","-u","/workspace/handler.py"]
