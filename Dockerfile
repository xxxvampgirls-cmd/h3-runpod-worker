FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 COMFY_DIR=/workspace/ComfyUI H3_COMFY_PORT=8188
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip git curl ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
RUN git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git
WORKDIR /workspace/ComfyUI
RUN python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt runpod
# H3 Studio's six model files are copied by BUILD_AND_PUSH.ps1 into build_context/models.
COPY build_context/models/ /workspace/ComfyUI/models/
COPY handler.py /workspace/handler.py
WORKDIR /workspace
CMD ["python3","/workspace/handler.py"]
