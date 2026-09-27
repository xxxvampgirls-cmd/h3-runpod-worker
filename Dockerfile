FROM nvidia/cuda:13.0.2-cudnn-runtime-ubuntu24.04

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
       --index-url https://download.pytorch.org/whl/cu130 \
       torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0

# Blackwell/RTX 50 optimization prerequisites. cu130 enables optimized CUDA kernels.
RUN python3 -m pip install --break-system-packages --no-cache-dir \
    "triton>=3.6" "nvidia-cutlass-dsl>=4.5" cuda-python apache-tvm-ffi

# H3 base weights are supplied by RunPod's Serverless Hugging Face Model Cache.
# Only the small Turbo LoRAs are fetched by the worker when needed; no paid Network Volume is required.
COPY handler.py /workspace/handler.py
WORKDIR /workspace
CMD ["python3","-u","/workspace/handler.py"]
