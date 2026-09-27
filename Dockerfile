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

# Optional optimization prerequisites; runtime stays on RunPod-compatible CUDA 12.8.
RUN python3 -m pip install --break-system-packages --no-cache-dir \
    "triton>=3.6" "nvidia-cutlass-dsl>=4.5" cuda-python apache-tvm-ffi

# H3-specific timestep cache: measured ~3x on 20-step H3 and requires no compiled CUDA extension.
RUN git clone --depth 1 https://github.com/Icyoung/ComfyUI-MiniMaxH3-TeaCache.git \
    /workspace/ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-TeaCache

# H3 base weights are supplied by RunPod's Serverless Hugging Face Model Cache.
# Only the small Turbo LoRAs are fetched by the worker when needed; no paid Network Volume is required.
COPY handler.py /workspace/handler.py
WORKDIR /workspace
CMD ["python3","-u","/workspace/handler.py"]
