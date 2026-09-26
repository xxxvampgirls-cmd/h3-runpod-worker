import base64, json, os, pathlib, subprocess, sys, threading, time, uuid
from urllib import request, parse
import runpod

COMFY_DIR=pathlib.Path(os.getenv('COMFY_DIR','/workspace/ComfyUI'))
HOST='127.0.0.1'; PORT=int(os.getenv('H3_COMFY_PORT','8188'))
BASE=f'http://{HOST}:{PORT}'
_proc=None; _lock=threading.Lock()

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
    wf=inp.get('workflow')
    if not isinstance(wf,dict) or not wf: return {'error':'input.workflow is empty'}
    ensure_comfy(); restore_images(inp.get('images'))
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
    if status.get('status_str')=='error': raise RuntimeError('ComfyUI workflow failed: '+json.dumps(status)[:3000])
    item=find_video(hist.get('outputs') or {})
    if not item: raise RuntimeError('Workflow completed but MP4/WebM/MOV was not found in ComfyUI history')
    raw=video_bytes(item)
    # Studio 1.1.1 accepts video_base64. Avoids requiring S3/object storage for first working build.
    return {'video_base64':base64.b64encode(raw).decode('ascii'),'filename':item.get('filename'),'bytes':len(raw)}

if __name__=='__main__':
    runpod.serverless.start({'handler': handler})
