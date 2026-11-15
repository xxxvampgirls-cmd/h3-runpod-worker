"""Locate original FreeVideo uploads by their exact recorded workflow paths.

This module is read-only. It DOES NOT copy, move or delete any reference file.
An exact upload path is required: matching a similar filename is not enough to
claim that an image was used in an old generation.
"""
import json
import os
import re
import stat
from pathlib import Path

IMAGE_EXT={".png",".jpg",".jpeg",".webp",".bmp",".tif",".tiff",".gif"}
UPLOAD_REF=re.compile(
    r"(?i)(?:^|[^A-Za-z0-9_-])(?:freevideo/)([0-9a-f]{32})/([^/\\:\"<>|?*\x00-\x1f]{1,180}\.(?:png|jpg|jpeg|webp|bmp|tiff?|gif))"
)
REPARSE=0x400

def ordinary_dir(path):
    try:
        info=os.lstat(path)
        return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not (
            getattr(info,"st_file_attributes",0)&REPARSE)
    except OSError:return False

def ordinary_file(path):
    try:
        info=os.lstat(path)
        return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not (
            getattr(info,"st_file_attributes",0)&REPARSE)
    except OSError:return False

def input_locations(output="",custom_nodes="",extra=""):
    """Only plausible sibling ComfyUI/input roots. Never recurse entire drives."""
    values=[output,custom_nodes,extra]
    roots=[]
    for value in values:
        if not value:continue
        p=Path(value).expanduser()
        possibilities=[p/"input", p.parent/"input"]
        if p.name.casefold()=="freevideo":
            possibilities.append(p.parent.parent/"input")
        if p.name.casefold()=="custom_nodes":
            possibilities.append(p.parent/"input")
        if p.name.casefold()=="promptdock_freevideo_bridge":
            possibilities.append(p.parent.parent/"input")
        if p.name.casefold()=="comfyui":
            possibilities.append(p/"input")
        for path in possibilities:
            if ordinary_dir(path) and path not in roots:
                roots.append(path)
    return roots

def load_small(path, limit=4_000_000):
    try:
        if not ordinary_file(path) or path.stat().st_size>limit:return {}
        value=json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value,dict) else {}
    except (OSError,UnicodeError,ValueError):return {}

def recorded_originals(workflow, media=None, *, limit=1600):
    """Extract only explicit 'freevideo/<upload-uuid>/<exact-image-name>' refs.

    A workflow may store widget values as embedded JSON strings. Never guess
    reference IDs by approximate filename, nor search unrelated input images.
    """
    matches=[]
    seen=set()
    visits=[0]
    def accept(s):
        if not isinstance(s,str) or len(s)>260_000:return
        s=s.replace("\\\\","/").replace("\\","/")
        for match in UPLOAD_REF.finditer(s):
            token=f"freevideo/{match.group(1).lower()}/{match.group(2)}"
            if token.casefold() not in seen:
                seen.add(token.casefold())
                matches.append(token)
    def walk(obj,depth=0):
        visits[0]+=1
        if depth>16 or visits[0]>limit:return
        if isinstance(obj,str):
            accept(obj)
            # Serialized FreeVideoMedia JSON may itself contain upload references.
            if obj.lstrip().startswith(("[","{")) and len(obj)<=100_000:
                try: walk(json.loads(obj),depth+1)
                except (ValueError,TypeError):pass
        elif isinstance(obj,dict):
            for k,v in list(obj.items())[:100]:
                if isinstance(k,str):accept(k)
                walk(v,depth+1)
        elif isinstance(obj,(tuple,list)):
            for v in obj[:180]:walk(v,depth+1)
    walk(workflow)
    if media:walk(media)
    return matches

def inspect_originals(run, *, output="",custom_nodes="",extra=""):
    """Returns path + availability for exact uploaded originals, without copies."""
    run=Path(run)
    workflow=load_small(run/"workflow.json")
    media=load_small(run/"media.json")
    tokens=recorded_originals(workflow,media)
    roots=input_locations(output,custom_nodes,extra)
    result=[]
    for token in tokens:
        parts=token.split("/")
        filename=parts[-1]
        entries=[]
        for root in roots:
            folder=root/"freevideo"/parts[1]
            if not ordinary_dir(folder):continue
            candidate=folder/filename
            if ordinary_file(candidate) and candidate.suffix.lower() in IMAGE_EXT:
                entries.append(candidate)
        if entries:
            for p in entries:
                result.append({"path":str(p),"label":filename,"kind":"original",
                               "exists":True,"source":"Загруженный оригинал"})
        else:
            result.append({"path":token.replace("/",os.sep),"label":filename,
                           "kind":"unresolved","exists":False,
                           "source":"Путь из workflow; оригинал не найден"})
    return result

def available_run_images(run):
    """Saved generation copies, not always byte-identical to source JPG files."""
    run=Path(run)
    names=["first.png","last.png"]
    names.extend(p.name for p in sorted(run.glob("reference-??.png"))
                 if re.fullmatch(r"reference-\d{2}\.png",p.name))
    result=[]
    for name in names:
        path=run/name
        if ordinary_file(path):
            try:
                if 0<path.stat().st_size<=80*1024*1024:
                    result.append({"path":str(path),"label":name,"kind":"snapshot",
                                   "exists":True,"source":"Копия, сохранённая FreeVideo"})
            except OSError:pass
    return result

def inspect_sources(run, *, output="",custom_nodes="",extra=""):
    return {"originals":inspect_originals(run,output=output,custom_nodes=custom_nodes,extra=extra),
            "snapshots":available_run_images(run)}
