"""Locate ComfyUI/custom_nodes without knowing a user's disk layout.

Bounded discovery, only plausible installations; no whole-drive recursion.
Never installs anything: the UI must ask for confirmation before writing.
"""
import os
import stat
import time
from pathlib import Path

REPARSE=0x400
POSSIBLE=("ComfyUI","ComfyUI_windows_portable","FreeVideo","FreeVideo-engine",
          "FreeVideo_engine","ComfyUI-Portable","H3-Models","AI","Apps","tools")
SKIP={"$recycle.bin","system volume information","windows","programdata","node_modules",
      ".git","site-packages","venv",".venv","users","appdata","models","checkpoints",
      "extensions","downloads","cache","temp","tmp","logs","output"}
KEYS=("freevideo","comfyui","engine","portable","studio","workspace","generative",
      "tools","apps","h3","python")
def directory(path):
    try:
        info=os.lstat(path)
        return stat.S_ISDIR(info.st_mode) and not (stat.S_ISLNK(info.st_mode)
                   or bool(getattr(info,"st_file_attributes",0)&REPARSE))
    except OSError:return False

def nodes_from_choice(value):
    """Accept choosing ComfyUI, its output, its custom_nodes, or parent FreeVideo."""
    if not value:return []
    path=Path(value).expanduser()
    roots=[path,*list(path.parents)[:5]]
    result=[]
    def add(p):
        if directory(p) and p.name.casefold()=="custom_nodes" and p not in result:
            result.append(p)
    for root in roots:
        add(root)
        add(root/"custom_nodes")
        add(root/"ComfyUI"/"custom_nodes")
        add(root/"comfyui"/"custom_nodes")
        add(root/"ComfyUI_windows_portable"/"ComfyUI"/"custom_nodes")
    if directory(path):
        try:
            for child in path.iterdir():
                if not directory(child):continue
                if "comfy" in child.name.casefold():
                    add(child/"custom_nodes")
                    add(child/"ComfyUI"/"custom_nodes")
        except OSError:pass
    return result

def discover(output="",saved="",limit_seconds=7,max_entries=6500):
    starts=[]
    def seed(value):
        if value:
            p=Path(value).expanduser()
            if p not in starts:starts.append(p)
    for value in (saved,output,os.environ.get("COMFYUI_PATH"),os.environ.get("FREEVIDEO_PATH")):
        seed(value)
    home=Path.home()
    for value in (home,home/"Desktop",home/"Documents",home/"Downloads",
                  os.environ.get("LOCALAPPDATA"),os.environ.get("PROGRAMFILES")):seed(value)
    # Windows 10: include secondary drives, but do not crawl them indiscriminately.
    for letter in "MDEFGHICJKNOPQRSTUVWXYZ":
        drive=Path(letter+":/")
        if not directory(drive):continue
        seed(drive)
        for name in POSSIBLE:seed(drive/name)
    result=[]
    seen=set()
    deadline=time.monotonic()+limit_seconds
    count=[0]
    def add(p):
        if directory(p) and p.name.casefold()=="custom_nodes":
            key=os.path.normcase(os.path.abspath(str(p)))
            if key not in seen:
                seen.add(key)
                result.append(str(p))
    for base in starts:
        if time.monotonic()>deadline:break
        for p in nodes_from_choice(base):add(p)
        if not directory(base):continue
        # A bounded breadth-first search only descends into likely installation names.
        queue=[(base,0)]
        visited=set()
        while queue and time.monotonic()<deadline and count[0]<max_entries:
            parent,depth=queue.pop(0)
            key=os.path.normcase(os.path.abspath(str(parent)))
            if key in visited:continue
            visited.add(key)
            try:
                for entry in os.scandir(parent):
                    count[0]+=1
                    if count[0]>=max_entries:break
                    if not entry.is_dir(follow_symlinks=False):continue
                    name=entry.name.casefold()
                    if name=="custom_nodes":
                        add(Path(entry.path));continue
                    if depth>=3 or name in SKIP or name.startswith("."):continue
                    # Always explore the first level of a user-selected path,
                    # then only install-specific names.
                    if depth==0 or any(t in name for t in KEYS):
                        queue.append((Path(entry.path),depth+1))
            except (PermissionError,OSError):pass
    return result
