"""Read-only audit and opt-in Windows Recycle Bin cleanup of FreeVideo online LoRA caches.

This is NOT a LoRA model deleter. It only recognizes folders named
vdn-online-lora-<24 hex> with a matching FreeVideo online_lora manifest.
Prepared models are hardlinked into these folders, so logical file size
greatly overstates bytes that would be freed when the Windows Recycle Bin
is emptied.

Safety: strict path validation; reject junctions/symlinks, missing or
unlisted files, partial builds, and caches used in the last 24 hours.
No automatic deletion. Never call unlink/rmtree. Every recycle request
revalidates the folder and its age.
"""
import json
import os
import re
import stat
import time
from pathlib import Path

NAME = re.compile(r"vdn-online-lora-[0-9a-f]{24}\Z")
SAFE_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
ADAPTER = re.compile(r"lora/(?:root|\d{2})\.safetensors\Z")
STABLE_HOURS = 24
MAX_MANIFEST = 16*1024*1024
REPARSE=0x400


def _stat(path):
    try:
        info=os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info,"st_file_attributes",0)&REPARSE:
        return None
    return info


def normal_dir(path):
    info=_stat(path)
    return bool(info and stat.S_ISDIR(info.st_mode))


def normal_file(path):
    info=_stat(path)
    return bool(info and stat.S_ISREG(info.st_mode))


def valid_cache_parent(path):
    """Only direct parents of official online LoRA variants, never root drives."""
    path=Path(path).expanduser()
    if not normal_dir(path):return False
    if path.name.casefold() not in ("cache","int8-convert","fp8-convert","int8-convrot","fp8-convrot","int8","fp8"):
        return False
    if path.parent==path or not normal_dir(path.parent):return False
    return True


def _read_manifest(folder):
    marker=Path(folder)/"manifest.json"
    if not normal_file(marker):return None
    try:
        if marker.stat().st_size>MAX_MANIFEST:return None
        with marker.open("r",encoding="utf-8") as stream:
            doc=json.load(stream)
    except (OSError,UnicodeError,ValueError,TypeError):
        return None
    if (not isinstance(doc,dict)
        or not isinstance(doc.get("lora_identity"),dict)
        or not isinstance(doc.get("online_lora"),dict)
        or not isinstance(doc.get("groups"),list)
        or not isinstance(doc.get("source_id"),str)
        or doc["source_id"][:24]!=folder.name[len("vdn-online-lora-"):]):
        return None
    return doc


def _listed_files(manifest):
    """Whitelist from the signed-by-name FreeVideo manifest, no inferred files."""
    names={"manifest.json"}
    groups=manifest.get("groups",[])
    if len(groups)>8000:return None
    for group in groups:
        if not isinstance(group,dict) or not isinstance(group.get("file"),str):return None
        name=group["file"]
        if not SAFE_PATH.fullmatch(name) or ".." in name.split("/"):return None
        names.add(name)
    for table in manifest.get("adaln_tables",[]):
        if not isinstance(table,dict):return None
        table_files=table.get("files",[])
        if not isinstance(table_files,list) or len(table_files)>2000:return None
        for entry in table_files:
            if not isinstance(entry,dict) or not isinstance(entry.get("file"),str):return None
            name=entry["file"]
            if not SAFE_PATH.fullmatch(name) or ".." in name.split("/"):return None
            names.add(name)
    return names


def inspect_variant(folder, parent=None, *, now=None, min_hours=STABLE_HOURS):
    """Fail closed. Checks every entry and counts hard links only once when unique."""
    now=time.time() if now is None else float(now)
    folder=Path(folder).expanduser()
    parent=Path(parent).expanduser() if parent is not None else folder.parent
    if (not valid_cache_parent(parent) or folder.parent!=parent
         or not NAME.fullmatch(folder.name) or not normal_dir(folder)):
        raise ValueError("Это не защищённая папка подготовленного кэша LoRA")
    doc=_read_manifest(folder)
    if doc is None:raise ValueError("Нет корректного manifest.json этой комбинации LoRA")
    names=_listed_files(doc)
    if not names:raise ValueError("Недопустимые пути в manifest.json")
    actual=set()
    total=0
    reclaim=0
    shared=0
    newest=0.0
    newest_manifest=(folder/"manifest.json").stat().st_mtime
    # Traverse regular files only. Reject any junctions, symlinks, unknown
    # entries, partial saves, and changed paths rather than following them.
    for current,dirs,files in os.walk(folder,topdown=True,followlinks=False):
        for directory in dirs:
            sub=Path(current)/directory
            if not normal_dir(sub):
                raise ValueError("Кэш содержит ссылку, junction или недоступный каталог")
        for name in files:
            p=Path(current)/name
            st=_stat(p)
            if st is None or not stat.S_ISREG(st.st_mode):
                raise ValueError("Кэш содержит неизвестные типы файлов")
            rel=p.relative_to(folder).as_posix()
            if rel not in names or rel.endswith(".partial"):
                raise ValueError("Неизвестный или незавершённый файл внутри кэша: "+rel[:80])
            actual.add(rel)
            newest=max(newest,st.st_mtime)
            total+=st.st_size
            if st.st_nlink==1:
                reclaim+=st.st_size
            else:
                shared+=st.st_size
    if actual!=names:
        raise ValueError("Файлы из manifest отсутствуют либо кэш не завершён")
    identity=doc["lora_identity"]
    adapters=identity.get("adapters",[])
    if not isinstance(adapters,list):adapters=[]
    display=[]
    for row in adapters[:6]:
        if isinstance(row,dict):
            # Adapter identity in FreeVideo may have only digest/name.
            label=str(row.get("name") or row.get("sha256") or row.get("id") or "LoRA")[:70]
            strength=row.get("strength")
            display.append(f"{label} ×{strength:g}" if type(strength) in (int,float) else label)
    idle_days=max(0,(now-newest_manifest)/86400)
    age_hours=(now-max(newest,newest_manifest))/3600
    status="готов к ручной очистке" if age_hours>=min_hours else "недавний / защищён"
    return dict(path=str(folder),name=folder.name,logical=total,
                reclaimable=reclaim,hardlinked=shared,last_used=newest_manifest,
                age_hours=age_hours,idle_days=idle_days,status=status,
                safe=age_hours>=min_hours,files=len(actual),adapters=display)


def scan_parent(parent, *, now=None, min_hours=STABLE_HOURS):
    parent=Path(parent).expanduser()
    if not valid_cache_parent(parent):
        raise ValueError("Выбери папку int8-convrot, int8-convert, fp8-convert или cache с vdn-online-lora-*")
    rows=[]
    skipped=[]
    for child in parent.iterdir():
        if not NAME.fullmatch(child.name):continue
        try:
            rows.append(inspect_variant(child,parent,now=now,min_hours=min_hours))
        except (OSError,ValueError,RuntimeError) as exc:
            skipped.append((child.name,str(exc)))
    rows.sort(key=lambda r:(r["safe"],r["reclaimable"],r["logical"]),reverse=True)
    return dict(path=str(parent),rows=rows,skipped=skipped,
                logical=sum(x["logical"] for x in rows),
                reclaimable=sum(x["reclaimable"] for x in rows if x["safe"]),
                protected=sum(not x["safe"] for x in rows))


def recycle_one(folder,parent,trash, *, now=None):
    """Explicit opt-in by GUI. Caller must confirm and FreeVideo must be closed."""
    record=inspect_variant(folder,parent,now=now,min_hours=STABLE_HOURS)
    if not record["safe"]:
        raise ValueError("Кэш использовался менее 24 часов назад; оставлен")
    trash(str(Path(record["path"])))
    return record


def discover_parents(hints=(), *, max_dirs=2800, seconds=8):
    """Bounded search by known FreeVideo names, not a full disk traversal."""
    deadline=time.monotonic()+seconds
    targets={"cache","int8-convert","fp8-convert","int8-convrot","fp8-convrot"}
    interest=("freevideo","mini","engine","models","vdn","h3","int8","fp8",
              "cache","prepared","convert","data","working","comfyui")
    starters=[]
    def add(value):
        if not value:return
        p=Path(value).expanduser()
        if p not in starters:starters.append(p)
    for value in hints:
        if not value:continue
        p=Path(value).expanduser()
        add(p)
        for par in list(p.parents)[:4]:
            if par.parent!=par:add(par)
    # User's M: drive is a known FreeVideo location. Check one layer
    # of plausibly named roots on mounted drives.
    if os.name=="nt":
        for letter in "MDEFGHICJKNOPQRSTUVWXYZ":
            drive=Path(letter+":/")
            if normal_dir(drive):add(drive)
    count=0
    found=[]
    visited=set()
    queue=[(p,0) for p in starters]
    while queue and time.monotonic()<deadline and count<max_dirs:
        p,depth=queue.pop(0)
        key=os.path.normcase(os.path.abspath(str(p)))
        if key in visited or not normal_dir(p):continue
        visited.add(key)
        try:
            dirs=[x for x in os.scandir(p) if x.is_dir(follow_symlinks=False)
                  and normal_dir(x.path)]
        except (OSError,PermissionError):continue
        count+=len(dirs)
        if p.name.casefold() in targets and any(NAME.fullmatch(x.name) for x in dirs):
            if p not in found:found.append(p)
            continue
        if depth>=7:continue
        for item in dirs:
            lower=item.name.casefold()
            if lower in (".git",".venv","venv","node_modules","site-packages",
                         "output","input","users","windows","downloads","temp","tmp",
                         "$recycle.bin","system volume information"):
                continue
            if depth==0 or any(x in lower for x in interest):
                queue.append((Path(item.path),depth+1))
    return found
