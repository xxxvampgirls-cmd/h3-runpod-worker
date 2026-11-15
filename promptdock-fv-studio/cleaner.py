"""Conservative, read-only-by-default FreeVideo disk analyzer.

Only sees FreeVideo run-owned output files in YYYY-MM-DD/<32 hex> folders.
Does not touch models, custom_nodes, downloads or ComfyUI cache.
Actual cleaning uses the Windows Recycle Bin, never unlinks input files.
"""
import json
import os
import re
import stat
import time
from pathlib import Path

DATE=re.compile(r"\d{4}-\d{2}-\d{2}\Z")
RUN=re.compile(r"[0-9a-f]{32}\Z")
ATTEMPT=re.compile(r"\d{2}-[0-9A-Za-z_-]+\Z")
INTERRUPTED=re.compile(r"\.(?:share|editor-export)-[0-9a-f]{32}\.mp4\Z")
DECODED={"rgb.npy","audio.npy","audio.wav"}
WORKING={"conditioning.pt","latents.pt","refine-input.pt"}
REPARSE=0x400
OLDER_SECONDS=3600

def real_dir(path):
    try:
        info=os.lstat(path)
        return stat.S_ISDIR(info.st_mode) and not (
            stat.S_ISLNK(info.st_mode) or getattr(info,"st_file_attributes",0)&REPARSE)
    except OSError:return False

def real_file(path):
    try:
        info=os.lstat(path)
        return stat.S_ISREG(info.st_mode) and not (
            stat.S_ISLNK(info.st_mode) or getattr(info,"st_file_attributes",0)&REPARSE)
    except OSError:return False

def fv_root(output):
    if not output:return None
    root=Path(output).expanduser()
    if root.name.lower()!="freevideo":root=root/"FreeVideo"
    if not real_dir(root) or not real_dir(root.parent):return None
    return root.resolve()

def read_record(path):
    try:
        if not real_file(path) or path.stat().st_size>4_194_304:return {}
        obj=json.loads(path.read_text(encoding="utf-8-sig"))
        return obj if isinstance(obj,dict) else {}
    except (OSError,UnicodeError,ValueError):return {}

def run_folder(root, path):
    """Avoid traversal or junctions even when an unexpected entry is present."""
    try:
        path=Path(path)
        if not (DATE.fullmatch(path.parent.name) and RUN.fullmatch(path.name)):return False
        if not real_dir(path) or not real_dir(path.parent):return False
        return path.parent.parent.resolve()==root
    except (OSError,ValueError):return False

def valid_artifact_location(root, run, path, kind):
    """Strict allowed set of generated temporary files, excluding unknown names."""
    try:
        if not run_folder(root,run) or not real_file(path):return False
        parts=Path(path).relative_to(run).parts
    except (OSError,ValueError):return False
    if kind=="export":
        return len(parts)==1 and bool(INTERRUPTED.fullmatch(parts[0]))
    if not parts or parts[0]!="video.artifacts":return False
    if not real_dir(run/"video.artifacts"):return False
    rest=parts[1:]
    if len(rest)>=3 and rest[0]=="attempts" and ATTEMPT.fullmatch(rest[1]):
        if not real_dir(run/"video.artifacts"/"attempts") or not real_dir(run/"video.artifacts"/"attempts"/rest[1]):
            return False
        rest=rest[2:]
    if kind=="decoded":
        if len(rest)==1 and rest[0] in DECODED:return True
        if len(rest)==2 and rest[0]=="media" and rest[1].endswith(".npy") and len(rest[1])<160:
            return real_dir(Path(path).parent)
    if kind=="working":
        return len(rest)==1 and rest[0] in WORKING
    return False

def candidates_in_run(root,run,age=OLDER_SECONDS,now=None):
    now=now or time.time()
    if not run_folder(root,run):return [],0,False
    request=read_record(run/"comfy-request.json")
    state=request.get("status")
    active=state in ("queued","starting","running")
    finished=state=="complete" and read_record(run/"video.request.json").get("success") is True and real_file(run/"video.mp4")
    result=read_record(run/"video.request.json") if finished else {}
    preview=bool(request.get("preview") or result.get("preview"))
    aborted=state in ("failed","cancelled") and not (run/"video.mp4").exists()
    files=[]
    if not active:
        artifacts=run/"video.artifacts"
        places=[artifacts] if real_dir(artifacts) else []
        attempts=artifacts/"attempts"
        if real_dir(attempts):
            for row in attempts.iterdir():
                if ATTEMPT.fullmatch(row.name) and real_dir(row):places.append(row)
        if finished:
            for place in places:
                for name in DECODED | (WORKING if not preview else set()):
                    path=place/name
                    kind="working" if name in WORKING else "decoded"
                    if not valid_artifact_location(root,run,path,kind):continue
                    try:
                        info=path.stat()
                        if now-info.st_mtime<age:continue
                        files.append(dict(path=str(path),bytes=info.st_size,kind=kind,run=str(run)))
                    except OSError:pass
                media=place/"media"
                if real_dir(media):
                    for path in media.glob("*.npy"):
                        if not valid_artifact_location(root,run,path,"decoded"):continue
                        try:
                            info=path.stat()
                            if now-info.st_mtime<age:continue
                            files.append(dict(path=str(path),bytes=info.st_size,kind="decoded",run=str(run)))
                        except OSError:pass
        # Only old interrupted share/editor copies with exact names. No snapshots.
        for path in run.glob(".*.mp4"):
            if not valid_artifact_location(root,run,path,"export"):continue
            try:
                info=path.stat()
                if now-info.st_mtime>=age:
                    files.append(dict(path=str(path),bytes=info.st_size,kind="export",run=str(run)))
            except OSError:pass
    # Show failed/cancelled run size for review, never auto-clean it.
    aborted_size=0
    if aborted:
        for parent,dirs,names in os.walk(run,followlinks=False):
            dirs[:]=[v for v in dirs if real_dir(Path(parent)/v)]
            for name in names:
                path=Path(parent)/name
                if real_file(path):
                    try:aborted_size+=path.stat().st_size
                    except OSError:pass
    return files,aborted_size,active

def scan(output,now=None,max_runs=5000):
    root=fv_root(output)
    totals=dict(decoded=0,working=0,export=0,aborted=0,active=0,
                aborted_bytes=0,files=0,runs=0,skipped=0)
    if root is None:return dict(root="",totals=totals,files=[],error="Не найден output/FreeVideo")
    files=[]
    for day in sorted(root.iterdir(),reverse=True):
        if not DATE.fullmatch(day.name) or not real_dir(day):continue
        for run in day.iterdir():
            if totals["runs"]>=max_runs:
                totals["skipped"]+=1;continue
            if not run_folder(root,run):continue
            totals["runs"]+=1
            candidates,aborted,active=candidates_in_run(root,run,now=now)
            if aborted:totals["aborted"]+=1;totals["aborted_bytes"]+=aborted
            if active:totals["active"]+=1
            for row in candidates:
                files.append(row);totals[row["kind"]]+=row["bytes"]
    totals["files"]=len(files)
    return dict(root=str(root),totals=totals,files=files,error="")

def recycle_selected(report, kinds, send_to_trash, now=None):
    """Return actual moved bytes. No unlink/rmtree; caller must confirm first."""
    root=Path(report.get("root",""))
    if not real_dir(root):return dict(count=0,bytes=0,failed=0)
    moved=0;bytes_=0;failed=0
    kinds=set(kinds)
    if not kinds.issubset({"decoded","working","export"}):return dict(count=0,bytes=0,failed=0)
    seen=set()
    for row in report.get("files",[]):
        if row.get("kind") not in kinds:continue
        path=Path(row["path"])
        if str(path) in seen:continue
        seen.add(str(path))
        run=Path(row["run"])
        try:
            if not valid_artifact_location(root,run,path,row["kind"]):continue
            fresh,_,active=candidates_in_run(root,run,now=now)
            if active or not any(x["path"]==str(path) and x["kind"]==row["kind"] for x in fresh):continue
            size=path.stat().st_size
            send_to_trash(str(path))
            moved+=1;bytes_+=size
        except (OSError,ValueError,PermissionError):
            failed+=1
    return dict(count=moved,bytes=bytes_,failed=failed)
