# H3 RunPod worker contract (Studio 1.1.0)

Studio sends jobs to `POST /v2/<endpoint>/run` with `input.workflow`, optional base64 `input.images`, and `input.h3` metadata.

The production worker must:
1. restore input images to ComfyUI input;
2. submit the API workflow;
3. wait for completion;
4. detect the generated MP4;
5. return `{ "video_url": "..." }` (preferred) or `{ "video_base64": "..." }`.

Do not use stock worker-comfyui as the final video worker until native MP4 output is supported. Keep model weights on RunPod persistent/network storage or an image cache, not in the Windows client.
