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
    'loras/minimax_h3_turbo_v4_step600_ema.safetensors',
]
MODEL_SOURCES={
    'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors': ('Comfy-Org/MiniMax-H3','diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors'),
    'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors': ('Comfy-Org/MiniMax-H3','text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'),
    'vae/minimax_h3_audio_vae_fp32.safetensors': ('Comfy-Org/MiniMax-H3','vae/minimax_h3_audio_vae_fp32.safetensors'),
    'vae/minimax_h3_video_vae_fp16.safetensors': ('Comfy-Org/MiniMax-H3','vae/minimax_h3_video_vae_fp16.safetensors'),
    'loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors': ('lightx2v/Minimax-h3-Turbo','minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors'),
    'loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors': ('lightx2v/Minimax-h3-Turbo','minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors'),
    'loras/minimax_h3_turbo_v4_step600_ema.safetensors': ('larryvrh/MiniMax-H3-Turbo-Lora','minimax_h3_turbo_v4_step600_ema.safetensors'),
}
MODEL_ROOT=pathlib.Path(os.getenv('H3_MODEL_ROOT','/workspace/h3-runtime-models'))
RUNPOD_HF_CACHE=pathlib.Path(os.getenv('HF_HOME','/runpod-volume/huggingface-cache'))/'hub'
BASE_REPO=os.getenv('MODEL_NAME','Comfy-Org/MiniMax-H3')
BASE_REVISION=os.getenv('MODEL_REVISION','')

def _cached_snapshot(repo_id):
    root=RUNPOD_HF_CACHE/('models--'+repo_id.replace('/','--'))/'snapshots'
    if BASE_REVISION:
        exact=root/BASE_REVISION
        if exact.is_dir():
            return exact
    if root.is_dir():
        snaps=sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p:p.stat().st_mtime, reverse=True)
        if snaps:
            return snaps[0]
    return None

def model_cache_diagnostics():
    """Return safe filesystem hints so we can locate RunPod's injected HF model cache."""
    roots=['/runpod-volume','/workspace','/root/.cache','/models','/model']
    found=[]
    needles=('minimax','h3','huggingface','models--comfy-org')
    for root in roots:
        p=pathlib.Path(root)
        if not p.exists():
            continue
        try:
            for base, dirs, files in os.walk(p):
                rel_depth=len(pathlib.Path(base).parts)-len(p.parts)
                if rel_depth > 5:
                    dirs[:] = []
                    continue
                low=base.lower()
                hits=[x for x in files if any(n in x.lower() for n in needles)]
                if any(n in low for n in needles) or hits:
                    found.append({'path':base,'files':hits[:20]})
                    if len(found)>=80:
                        return found
        except Exception as e:
            found.append({'path':root,'error':type(e).__name__})
    return found

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
    'MiniMaxH3TurboLoRA','MiniMaxH3TurboSampler',
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
            'service':'h3-runpod-worker','version':'0.6.5'}

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

def video_thumbnail_base64(raw):
    """Extract a compact JPEG preview on the worker; Docker already includes ffmpeg."""
    import tempfile
    root=pathlib.Path(tempfile.mkdtemp(prefix='h3_thumb_'))
    try:
        src=root/'video.mp4'; dst=root/'preview.jpg'; src.write_bytes(raw)
        cp=subprocess.run(
            ['ffmpeg','-y','-ss','0.35','-i',str(src),'-frames:v','1','-vf','scale=360:-2','-q:v','3',str(dst)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45
        )
        if cp.returncode==0 and dst.is_file() and dst.stat().st_size>100:
            return base64.b64encode(dst.read_bytes()).decode('ascii')
    except Exception as e:
        print('thumbnail warning:', type(e).__name__, str(e)[:300], flush=True)
    finally:
        import shutil
        shutil.rmtree(root,ignore_errors=True)
    return ''

def handler(job):
    inp=(job or {}).get('input') or {}
    if inp.get('healthcheck') is True:
        gpu={}
        try:
            import torch
            if torch.cuda.is_available():
                p=torch.cuda.get_device_properties(0)
                free,total=torch.cuda.mem_get_info(0)
                gpu={'name':p.name,'total_vram_gb':round(total/(1024**3),2),'free_vram_gb':round(free/(1024**3),2),'torch':torch.__version__,'torch_cuda':torch.version.cuda}
            else:
                gpu={'cuda_available':False}
        except Exception as e:
            gpu={'diagnostic_error':str(e)}
        return {'ok':True,'service':'h3-runpod-worker','version':'0.6.5','models_required':len(REQUIRED_MODELS),'model_root':str(MODEL_ROOT),'base_model_store':BASE_REPO,'gpu':gpu}
    if inp.get('diagnose_model_cache') is True:
        return {
            'ok': True,
            'service': 'h3-runpod-worker',
            'version': '0.5.4',
            'env_hints': {k:v for k,v in os.environ.items() if any(x in k.upper() for x in ('MODEL','HF_','HUGGING','RUNPOD')) and 'TOKEN' not in k.upper() and 'KEY' not in k.upper() and 'SECRET' not in k.upper()},
            'paths': model_cache_diagnostics()
        }
    if inp.get('preflight') is True:
        return preflight()

    wf=inp.get('workflow')
    if not isinstance(wf,dict) or not wf:
        return {'error':'input.workflow is empty'}
    worker_t0=time.time()
    effective_prompt=''

    # AUTO SCENE hook. The desktop app (or any vision-capable front end) may send
    # a structured scene plan derived from the start image. Keep it separate from
    # the user's short idea so motion, dialogue and ambience can be controlled
    # independently without hard-coding one specific prompt-node id.
    auto_scene = inp.get('auto_scene') or {}
    if auto_scene.get('enabled'):
        visual_prompt = str(auto_scene.get('visual_prompt') or '').strip()
        dialogue = str(auto_scene.get('dialogue') or '').strip()
        ambience = str(auto_scene.get('ambience') or '').strip()
        combined = visual_prompt
        if dialogue:
            combined += "\nSpoken dialogue: " + dialogue
        if ambience:
            combined += "\nNatural synchronized audio/ambience: " + ambience
        if combined:
            effective_prompt=combined
            # Replace only explicitly tagged prompt nodes. This avoids accidentally
            # overwriting negative prompts or unrelated CLIP text nodes.
            tagged = 0
            for nid,node in wf.items():
                meta = node.get('_meta') or {}
                title = str(meta.get('title') or '').lower()
                if ('h3 prompt' in title or 'auto scene prompt' in title):
                    inputs = node.setdefault('inputs', {})
                    for key in ('text','prompt'):
                        if key in inputs:
                            inputs[key] = combined
                            tagged += 1
                            break
            print(f"AUTO SCENE enabled: tagged_prompt_nodes={tagged} dialogue={bool(dialogue)} ambience={bool(ambience)}", flush=True)

    ensure_models(); ensure_comfy(); restore_images(inp.get('images'))

    def run_workflow(one_wf):
        client='h3-runpod-'+uuid.uuid4().hex
        ans=_http('/prompt',{'prompt':one_wf,'client_id':client},timeout=30)
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
        if not item: raise RuntimeError('Workflow completed but video was not found')
        return video_bytes(item)

    h3=inp.get('h3') or {}

    # MAX SPEED profile: use the dedicated H3 Turbo nodes rather than a stock
    # sampler with fewer steps. The Turbo sampler handles H3's video/audio clocks
    # correctly and is the intended fast path for the Turbo LoRA.
    max_speed = h3.get('max_speed', True) is not False
    actual_profile = 'quality-reference'
    if max_speed:
        target_steps = 4
        turbo_lora = 'minimax_h3_turbo_v4_step600_ema.safetensors'

        # Replace the generic LoRA loader with the H3 Turbo LoRA node.
        # On <=40 GB GPUs use merge mode to reduce peak VRAM and remove the
        # per-layer LoRA bypass overhead during sampling.
        try:
            import torch
            _profile_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3) if torch.cuda.is_available() else 0
        except Exception:
            _profile_vram_gb = 0
        low_vram_mode = bool(_profile_vram_gb and _profile_vram_gb <= 40)

        replaced_lora = 0
        replaced_sampler = 0
        for nid,node in wf.items():
            ctype = node.get('class_type')
            inputs = node.setdefault('inputs', {})
            if ctype == 'LoraLoaderModelOnly':
                model_in = inputs.get('model')
                if model_in is not None:
                    node['class_type'] = 'MiniMaxH3TurboLoRA'
                    node['inputs'] = {
                        'model': model_in,
                        'lora_name': turbo_lora,
                        'strength': 1.0,
                        'low_vram': low_vram_mode,
                    }
                    replaced_lora += 1
            elif ctype == 'KSamplerSelect':
                # Same SAMPLER output socket, but with the H3-specific dual
                # video/audio schedule instead of stock Euler selection.
                node['class_type'] = 'MiniMaxH3TurboSampler'
                node['inputs'] = {}
                replaced_sampler += 1
            elif ctype == 'BasicScheduler':
                inputs['scheduler'] = 'simple'
                inputs['steps'] = target_steps
                if 'denoise' in inputs:
                    inputs['denoise'] = 1.0

        # TeaCache is intentionally not inserted at four steps. With only four
        # denoising passes there is little reuse to exploit and the cache bookkeeping
        # can erase part of the gain. Keep the graph as lean as possible.
        actual_profile = 'h3-turbo-4step-v4'
        print(
            f"H3 TURBO FAST: steps={target_steps} lora={turbo_lora} "
            f"lora_nodes={replaced_lora} sampler_nodes={replaced_sampler} "
            f"low_vram={low_vram_mode}",
            flush=True,
        )

    duration=int(h3.get('duration') or 5)
    # RTX 5090 32 GB cannot safely hold a native 10-20s 768p H3 latent in one pass.
    # Long clips are therefore generated as <=5s continuation segments and joined.
    resolution=str(h3.get('resolution') or '').lower()
    # 480p long mode: try one native continuous H3 pass (better dialogue/lip continuity).
    # Higher resolutions stay on the safe segmented path for 32 GB GPUs.
    # Native long generation on large-VRAM GPUs (RTX PRO 6000 96GB / H100-class and above).
    # 32GB workers keep the safe segmented fallback at >480p.
    try:
        import torch
        gpu_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3) if torch.cuda.is_available() else 0
    except Exception:
        gpu_vram_gb = 0
    # 15s 768p QUALITY still OOMs on RTX PRO 6000 96GB (94.97 GiB device limit).
    # Keep high-resolution long clips on the proven 5s segmented path.
    # Native long is reserved for 480p until a lower-memory native workflow is validated.
    native_long = resolution == '480p'
    mode = 'native' if native_long else ('segmented' if duration > 5 else 'native-short')
    print(f"H3 mode: duration={duration}s resolution={resolution} vram={gpu_vram_gb:.1f}GB mode={mode}", flush=True)
    if duration > 5 and not native_long:
        import copy, math, tempfile
        # Fastest safe segmentation: prefer 2 chunks on high-VRAM workers.
        # 15s -> ~7.5s + ~7.5s on >=80GB; 32GB keeps proven <=5s chunks.
        seg_count = 2 if gpu_vram_gb >= 80 and duration <= 15 else max(2, math.ceil(duration/5))
        work=pathlib.Path(tempfile.mkdtemp(prefix='h3_segments_'))
        parts=[]
        prev_frame=None
        try:
            for idx in range(seg_count):
                one=copy.deepcopy(wf)
                # Set per-segment frame count dynamically and snap to H3's 17k+5 grid.
                seg_seconds = duration / seg_count
                seg_frames = max(22, round(seg_seconds * 24))
                seg_frames += (5 - (seg_frames % 17)) % 17
                h3_node = next((nid for nid,n in one.items() if n.get('class_type')=='MiniMaxH3ImageToVideo'), None)
                if h3_node:
                    one[h3_node]['inputs']['length']=seg_frames
                    if prev_frame:
                        node_id='900'
                        one[node_id]={'class_type':'LoadImage','inputs':{'image':prev_frame.name}}
                        one[h3_node]['inputs']['first_frame']=[node_id,0]
                        # Never force the user's original LAST FRAME onto intermediate chunks.
                        one[h3_node]['inputs'].pop('last_frame',None)
                raw=run_workflow(one)
                part=work/f'part_{idx:02d}.mp4'; part.write_bytes(raw); parts.append(part)
                if idx < seg_count-1:
                    frame=COMFY_DIR/'input'/f'h3_continue_{uuid.uuid4().hex}.png'
                    subprocess.run(['ffmpeg','-y','-sseof','-0.08','-i',str(part),'-frames:v','1',str(frame)],
                                   check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    prev_frame=frame
            concat=work/'concat.txt'
            concat.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in parts))
            final=work/'final.mp4'
            subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(final)],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            raw=final.read_bytes()
            try:
                import torch
                gpu_name=torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''
            except Exception:
                gpu_name=''
            return {'video_base64':base64.b64encode(raw).decode('ascii'),
                    'thumbnail_base64':video_thumbnail_base64(raw),
                    'filename':'H3_Studio_long.mp4','bytes':len(raw),
                    'segments':seg_count,'segment_seconds':round(duration/seg_count,2),
                    'continuation':'last_frame','mode':'fast-segmented',
                    'profile':actual_profile,'prompt_used':effective_prompt,
                    'worker_seconds':round(time.time()-worker_t0,2),'gpu':gpu_name}
        finally:
            import shutil
            shutil.rmtree(work,ignore_errors=True)

    raw=run_workflow(wf)
    try:
        import torch
        gpu_name=torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''
    except Exception:
        gpu_name=''
    return {'video_base64':base64.b64encode(raw).decode('ascii'),
            'thumbnail_base64':video_thumbnail_base64(raw),
            'filename':'H3_Studio.mp4','bytes':len(raw),
            'mode':mode,'profile':actual_profile,'prompt_used':effective_prompt,
            'worker_seconds':round(time.time()-worker_t0,2),'gpu':gpu_name}

runpod.serverless.start({"handler": handler})
