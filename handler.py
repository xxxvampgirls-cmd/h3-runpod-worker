import base64, json, os, pathlib, subprocess, sys, threading, time, uuid
from urllib import request, parse
import runpod
from huggingface_hub import hf_hub_download

COMFY_DIR=pathlib.Path(os.getenv('COMFY_DIR','/workspace/ComfyUI'))
HOST='127.0.0.1'; PORT=int(os.getenv('H3_COMFY_PORT','8188'))
BASE=f'http://{HOST}:{PORT}'
_proc=None; _lock=threading.Lock()

REQUIRED_MODELS=[
    'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors',
    'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
    'vae/minimax_h3_audio_vae_fp32.safetensors',
    'vae/minimax_h3_video_vae_fp16.safetensors',
    'loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors',
    'loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors',
]
MODEL_SOURCES={
    'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors': ('Comfy-Org/MiniMax-H3','diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors'),
    'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors': ('Comfy-Org/MiniMax-H3','text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'),
    'vae/minimax_h3_audio_vae_fp32.safetensors': ('Comfy-Org/MiniMax-H3','vae/minimax_h3_audio_vae_fp32.safetensors'),
    'vae/minimax_h3_video_vae_fp16.safetensors': ('Comfy-Org/MiniMax-H3','vae/minimax_h3_video_vae_fp16.safetensors'),
    'loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors': ('lightx2v/Minimax-h3-Turbo','minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors'),
    'loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors': ('lightx2v/Minimax-h3-Turbo','minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors'),
}
MODEL_ROOT=pathlib.Path(os.getenv('H3_MODEL_ROOT','/workspace/h3-runtime-models'))
RUNPOD_HF_CACHE=pathlib.Path('/runpod-volume/huggingface-cache/hub')
BASE_REPO='Comfy-Org/MiniMax-H3'

def _cached_snapshot(repo_id):
    exact=RUNPOD_HF_CACHE/('models--'+repo_id.replace('/','--'))/'snapshots'
    candidates=[exact, RUNPOD_HF_CACHE/('models--'+repo_id.replace('/','--')).lower()/'snapshots']
    for root in candidates:
        if root.is_dir():
            snaps=sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p:p.stat().st_mtime, reverse=True)
            if snaps:
                return snaps[0]
    return None

def ensure_models():
    """Use RunPod Model Store for the large H3 base repo; fetch only the two Turbo LoRAs."""
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    cached=_cached_snapshot(BASE_REPO)
    if cached is None:
        raise RuntimeError(
            "RunPod cached model not found. Set endpoint Model to Comfy-Org/MiniMax-H3 "
            "so RunPod downloads/caches it before starting the worker."
        )

    for rel,(repo_id,filename) in MODEL_SOURCES.items():
        comfy_target=COMFY_DIR/'models'/rel
        comfy_target.parent.mkdir(parents=True, exist_ok=True)

        if repo_id == BASE_REPO:
            target=cached/filename
            if not target.is_file():
                raise RuntimeError(f"Cached H3 file missing: {filename}")
        else:
            target=MODEL_ROOT/rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file():
                downloaded=pathlib.Path(hf_hub_download(
                    repo_id=repo_id, filename=filename, local_dir=str(MODEL_ROOT)
                ))
                if downloaded != target and not target.exists():
                    downloaded.replace(target)

        if comfy_target.exists() or comfy_target.is_symlink():
            if comfy_target.is_symlink() and comfy_target.resolve()==target.resolve():
                continue
            if comfy_target.is_file() or comfy_target.is_symlink():
                comfy_target.unlink()
        comfy_target.symlink_to(target)

REQUIRED_NODES={
    'UNETLoader','CLIPLoader','VAELoader','LoraLoaderModelOnly',
    'MiniMaxH3SigmaShift','MiniMaxH3ImageToVideo','RandomNoise',
    'BasicGuider','KSamplerSelect','BasicScheduler','SamplerCustomAdvanced',
    'VAEDecode','VAEDecodeAudio','CreateVideo','SaveVideo'
}

def _http(path, data=None, timeout=30):
    body=None if data is None else json.dumps(data).encode()
    req=request.Request(BASE+path,data=body,headers={'Content-Type':'application/json'} if body else {},method='POST' if body else 'GET')
    with request.urlopen(req,timeout=timeout) as r:
        raw=r.read(); return json.loads(raw.decode()) if raw else {}

def ensure_comfy():
    global _proc
    with _lock:
        try:
            _http('/system_stats',timeout=2); return
        except Exception: pass
        if _proc is None or _proc.poll() is not None:
            cmd=[sys.executable,'main.py','--listen',HOST,'--port',str(PORT),'--disable-auto-launch']
            _proc=subprocess.Popen(cmd,cwd=str(COMFY_DIR),stdout=sys.stdout,stderr=sys.stderr)
        deadline=time.time()+180
        while time.time()<deadline:
            if _proc.poll() is not None: raise RuntimeError(f'ComfyUI exited rc={_proc.returncode}')
            try: _http('/system_stats',timeout=2); return
            except Exception: time.sleep(1)
        raise TimeoutError('ComfyUI readiness timeout')

def preflight():
    ensure_models()
    missing_models=[p for p in REQUIRED_MODELS if not (COMFY_DIR/'models'/p).is_file()]
    if missing_models:
        return {'ok':False,'stage':'models','missing_models':missing_models}
    ensure_comfy()
    info=_http('/object_info',timeout=30)
    missing_nodes=sorted(REQUIRED_NODES-set(info.keys()))
    return {'ok':not missing_nodes,'stage':'ready' if not missing_nodes else 'nodes',
            'models':len(REQUIRED_MODELS),'missing_nodes':missing_nodes,
            'service':'h3-runpod-worker','version':'0.5.0'}

def restore_images(items):
    d=COMFY_DIR/'input'; d.mkdir(parents=True,exist_ok=True)
    for item in items or []:
        name=pathlib.Path(item.get('name') or f'h3_{uuid.uuid4().hex}.png').name
        b64=item.get('image') or item.get('base64')
        if not b64: continue
        if ',' in b64 and b64.lstrip().startswith('data:'): b64=b64.split(',',1)[1]
        (d/name).write_bytes(base64.b64decode(b64))

def find_video(outputs):
    for node in (outputs or {}).values():
        for key in ('videos','gifs','images'):
            for item in node.get(key,[]) or []:
                fn=item.get('filename','')
                if fn.lower().endswith(('.mp4','.webm','.mov')): return item
    return None

def video_bytes(item):
    q=parse.urlencode({'filename':item['filename'],'subfolder':item.get('subfolder',''),'type':item.get('type','output')})
    with request.urlopen(BASE+'/view?'+q,timeout=300) as r: return r.read()

def handler(job):
    inp=(job or {}).get('input') or {}
    if inp.get('healthcheck') is True:
        return {'ok':True,'service':'h3-runpod-worker','version':'0.5.0','models_required':len(REQUIRED_MODELS),'model_root':str(MODEL_ROOT),'base_model_store':BASE_REPO}
    if inp.get('preflight') is True:
        return preflight()

    wf=inp.get('workflow')
    if not isinstance(wf,dict) or not wf:
        return {'error':'input.workflow is empty'}

    ensure_models(); ensure_comfy(); restore_images(inp.get('images'))
    client='h3-runpod-'+uuid.uuid4().hex
    ans=_http('/prompt',{'prompt':wf,'client_id':client},timeout=30)
    pid=ans.get('prompt_id')
    if not pid: raise RuntimeError('ComfyUI did not return prompt_id: '+str(ans)[:1000])
    deadline=time.time()+int(os.getenv('H3_JOB_TIMEOUT','1800'))
    hist=None
    while time.time()<deadline:
        h=_http('/history/'+parse.quote(pid),timeout=30)
        if pid in h: hist=h[pid]; break
        time.sleep(1)
    if hist is None: raise TimeoutError('ComfyUI workflow timeout')
    status=hist.get('status') or {}
    if status.get('status_str')=='error':
        raise RuntimeError('ComfyUI workflow failed: '+json.dumps(status)[:3000])
    item=find_video(hist.get('outputs') or {})
    if not item:
        raise RuntimeError('Workflow completed but MP4/WebM/MOV was not found in ComfyUI history')
    raw=video_bytes(item)
    return {'video_base64':base64.b64encode(raw).decode('ascii'),'filename':item.get('filename'),'bytes':len(raw)}

runpod.serverless.start({"handler": handler})
