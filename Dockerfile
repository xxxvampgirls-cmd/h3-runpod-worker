FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    COMFY_DIR=/workspace/ComfyUI \
    H3_COMFY_PORT=8188 \
    HF_HOME=/workspace/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git curl ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
RUN git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git

WORKDIR /workspace/ComfyUI
RUN python3 -m pip install --break-system-packages --no-cache-dir \
    -r requirements.txt runpod "huggingface_hub[cli]"

# MiniMax-H3 base weights used by the official ComfyUI H3 workflows.
# Bake them into the image so Serverless cold starts do not re-download ~40 GB.
RUN mkdir -p models/diffusion_models models/text_encoders models/vae models/loras \
    && hf download Comfy-Org/MiniMax-H3 \
       diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors \
       text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
       vae/minimax_h3_audio_vae_fp32.safetensors \
       vae/minimax_h3_video_vae_fp16.safetensors \
       --local-dir /tmp/h3-base \
    && mv /tmp/h3-base/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors models/diffusion_models/ \
    && mv /tmp/h3-base/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors models/text_encoders/ \
    && mv /tmp/h3-base/vae/minimax_h3_audio_vae_fp32.safetensors models/vae/ \
    && mv /tmp/h3-base/vae/minimax_h3_video_vae_fp16.safetensors models/vae/ \
    && rm -rf /tmp/h3-base /workspace/.cache/huggingface

# LightX2V Turbo LoRAs: FAST 4-step and QUALITY 8-step.
RUN hf download lightx2v/Minimax-h3-Turbo \
       minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors \
       minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors \
       --local-dir models/loras \
    && rm -rf /workspace/.cache/huggingface

COPY handler.py /workspace/handler.py
WORKDIR /workspace
CMD ["python3","-u","/workspace/handler.py"]
