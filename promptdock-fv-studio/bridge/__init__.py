"""PromptDock one-way local handoff for FreeVideo; never starts a generation.

Drop this folder into ComfyUI/custom_nodes/ and restart ComfyUI.
The companion application writes pending requests under queue/; the browser tab
applies an explicit user request to the currently visible FreeVideo workflow.
"""
import json
import time
from pathlib import Path
from aiohttp import web
from server import PromptServer

ROOT=Path(__file__).parent
QUEUE=ROOT/"queue"
PENDING=QUEUE/"pending.json"
ACK=QUEUE/"ack.json"
MAX_BYTES=160_000

def safe_data(path):
    try:
        if path.is_symlink() or path.stat().st_size>MAX_BYTES:return None
        obj=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj,dict):return None
        return obj
    except (OSError,ValueError,UnicodeError):return None

@PromptServer.instance.routes.get("/promptdock-bridge/pending")
async def pending(request):
    doc=safe_data(PENDING)
    if not doc or doc.get("expires",0)<time.time():
        return web.json_response({"pending":False},headers={"Cache-Control":"no-store"})
    return web.json_response({"pending":True,"request":doc},headers={"Cache-Control":"no-store"})

@PromptServer.instance.routes.post("/promptdock-bridge/ack")
async def acknowledge(request):
    # Browser-side execution only; this endpoint cannot launch a generation.
    try:
        content=await request.json()
        doc=safe_data(PENDING)
        if not doc or not isinstance(content,dict) or content.get("id")!=doc.get("id"):
            return web.json_response({"ok":False},status=409)
        value=dict(id=doc["id"],ok=content.get("ok") is True,
                   message=str(content.get("message",""))[:500],done=time.time())
        QUEUE.mkdir(parents=True,exist_ok=True)
        temp=ACK.with_suffix(".tmp")
        temp.write_text(json.dumps(value,ensure_ascii=False),encoding="utf-8")
        temp.replace(ACK)
        PENDING.unlink(missing_ok=True)
        return web.json_response({"ok":True})
    except (ValueError,TypeError,OSError,UnicodeError):
        return web.json_response({"ok":False},status=400)

NODE_CLASS_MAPPINGS={}
WEB_DIRECTORY="./web"
