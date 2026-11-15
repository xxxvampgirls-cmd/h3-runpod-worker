"""Read-only FreeVideo disk audit and strictly bounded cleanup helpers.

Never remove model files, previews for upscales, or active creations.
The only direct FreeVideo working-file cleanup is its own local HTTP endpoint.
"""
import json
import os
import re
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path

ID = re.compile(r"[0-9a-f]{32}\Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
REPARSE = 0x400
DECODED = {"rgb.npy", "audio.npy", "audio.wav"}
WORKING = {"conditioning.pt", "latents.pt", "refine-input.pt"}
ATTEMPT = re.compile(r"\d{2}-[0-9A-Za-z_-]+\Z")
INTERRUPTED = re.compile(r"\.(?:share|editor-export)-[0-9a-f]{32}\.mp4\Z")


def reparse(path):
    try:
        v = os.lstat(path)
        return stat.S_ISLNK(v.st_mode) or bool(getattr(v, "st_file_attributes", 0) & REPARSE)
    except OSError:
        return True


def root_dir(output):
    root = Path(output or "").expanduser()
    if root.name.casefold() != "freevideo":
        root = root / "FreeVideo"
    if not root.is_dir() or reparse(root):
        raise ValueError("Выбери папку output или output/FreeVideo")
    return root.resolve(strict=True)


def run_folders(root):
    """Yield the exact official date / UUID layout, without following links."""
    for day in sorted(root.iterdir()):
        if not DATE.fullmatch(day.name) or not day.is_dir() or reparse(day):
            continue
        for run in day.iterdir():
            if ID.fullmatch(run.name) and run.is_dir() and not reparse(run):
                yield run


def small_json(path):
    try:
        if reparse(path) or path.stat().st_size > 4 * 1024 * 1024:
            return {}
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}


def tree_size(run):
    total = 0
    newest = 0.
    unsafe = False
    entries = []
    # Visit metadata only. Do not follow junctions or symbolic links.
    for base, dirs, files in os.walk(run, followlinks=False):
        for name in dirs[:]:
            path = Path(base) / name
            if reparse(path):
                dirs.remove(name)
                unsafe = True
        for name in files:
            path = Path(base) / name
            if reparse(path):
                unsafe = True
                continue
            try:
                st = path.stat()
                total += st.st_size
                newest = max(newest, st.st_mtime)
                entries.append((path, st.st_size))
            except OSError:
                unsafe = True
    return total, newest, unsafe, entries


def find_reclaimable(run, entries, preview=False):
    """Informational estimate only; FreeVideo's API decides what may be deleted."""
    total = 0
    for path, size in entries:
        relative = path.relative_to(run)
        p = relative.parts
        if len(p) < 2 or p[0] != "video.artifacts":
            continue
        tail = p[1:]
        if len(tail) == 1 and tail[0] in DECODED:
            total += size
        if len(tail) == 1 and tail[0] in WORKING and not preview:
            total += size
        if len(tail) == 2 and tail[0] == "media" and tail[1].lower().endswith(".npy"):
            total += size
        if len(tail) == 3 and tail[0] == "attempts" and ATTEMPT.fullmatch(tail[1]):
            if tail[2] in DECODED or (tail[2] in WORKING and not preview):
                total += size
        if len(tail) == 4 and tail[0] == "attempts" and ATTEMPT.fullmatch(tail[1]):
            if tail[2] == "media" and tail[3].lower().endswith(".npy"):
                total += size
    return total


def inspect(output, now=None):
    """Return file-size breakdown without mutating any FreeVideo output."""
    now = time.time() if now is None else now
    root = root_dir(output)
    totals = {"runs": 0, "videos": 0, "all_bytes": 0, "video_bytes": 0,
              "working_estimate": 0, "failed_count": 0, "failed_bytes": 0,
              "other_bytes": 0, "failed": [], "large_runs": []}
    for run in run_folders(root):
        totals["runs"] += 1
        folder_bytes, newest, unsafe, entries = tree_size(run)
        totals["all_bytes"] += folder_bytes
        video = run / "video.mp4"
        has_video = video.is_file() and not reparse(video) and video.stat().st_size > 0
        if has_video:
            totals["videos"] += 1
            totals["video_bytes"] += video.stat().st_size
            request = small_json(run / "comfy-request.json")
            report = small_json(run / "video.request.json")
            if report.get("success") is True and request.get("status") == "complete" and not unsafe:
                totals["working_estimate"] += find_reclaimable(run, entries, preview=report.get("preview") is True)
        else:
            request = small_json(run / "comfy-request.json")
            if not unsafe and request.get("status") in ("failed", "cancelled") and (
                    newest and newest < now - 24 * 3600):
                totals["failed_count"] += 1
                totals["failed_bytes"] += folder_bytes
                totals["failed"].append({"path": str(run), "bytes": folder_bytes,
                                          "recovery": recovery_evidence(run),
                                          "status": request.get("status","failed")})
        if folder_bytes:
            totals["large_runs"].append({"name": run.parent.name + "/" + run.name[:10], "bytes": folder_bytes})
    totals["large_runs"] = sorted(totals["large_runs"], key=lambda x:x["bytes"], reverse=True)[:6]
    totals["other_bytes"] = max(0, totals["all_bytes"] - totals["video_bytes"] -
                                totals["working_estimate"] - totals["failed_bytes"])
    return totals



def recovery_evidence(folder):
    """Informational warning: some unfinished runs retain useful input and latent data.

    Do not claim a failed run can be resumed. Look at known attempt folders too;
    otherwise a failed retry would be misclassified as having nothing to keep.
    """
    folder=Path(folder)
    known=("first.png","last.png","frame-preview.jpg","input-conditioning.pt",
           "continuation-source.safetensors","video.artifacts/latents.pt",
           "video.artifacts/conditioning.pt","video.artifacts/refine-input.pt",
           "video.artifacts/rgb.npy","video.artifacts/audio.npy")
    result=[]
    for name in known:
        file=folder/name
        try:
            if file.is_file() and not reparse(file) and file.stat().st_size>0:
                result.append(name)
        except OSError:pass
    for entry in sorted(folder.glob("reference-??.png")):
        if not re.fullmatch(r"reference-\d{2}\.png",entry.name):continue
        try:
            if entry.is_file() and not reparse(entry) and entry.stat().st_size>0:
                result.append(entry.name)
        except OSError:pass
        if len(result)>=12:return result[:12]
    attempts=folder/"video.artifacts"/"attempts"
    if attempts.is_dir() and not reparse(attempts):
        for attempt in sorted(attempts.iterdir()):
            if not ATTEMPT.fullmatch(attempt.name) or not attempt.is_dir() or reparse(attempt):
                continue
            for name in ("latents.pt","conditioning.pt","refine-input.pt","rgb.npy","audio.npy"):
                file=attempt/name
                try:
                    if file.is_file() and not reparse(file) and file.stat().st_size>0:
                        result.append("video.artifacts/attempts/"+attempt.name+"/"+name)
                except OSError:pass
                if len(result)>=12:return result[:12]
    return result[:12]


def eligible_failed(folder, root, now=None):
    """Revalidate a failed creation before any caller considers deletion."""
    now = time.time() if now is None else now
    root = root_dir(root)
    folder = Path(folder)
    if (not ID.fullmatch(folder.name) or not DATE.fullmatch(folder.parent.name) or
            folder.parent.parent.resolve(strict=True) != root or
            any(reparse(item) for item in (folder,folder.parent))):
        raise ValueError("Папка вне проверенного каталога FreeVideo")
    if (folder / "video.mp4").exists() or not (folder / "comfy-request.json").is_file():
        raise ValueError("Нельзя удалять папку, содержащую видео или без журнала")
    record = small_json(folder / "comfy-request.json")
    if record.get("status") not in ("failed", "cancelled"):
        raise ValueError("Это не завершённая ошибкой генерация")
    amount, newest, unsafe, entries = tree_size(folder)
    if unsafe or not newest or newest >= now - 24 * 3600:
        raise ValueError("Папка изменялась недавно или содержит ссылки")
    # Be more conservative than FreeVideo itself: never delete unknown
    # top-level files or folders, even if they are inside a valid run ID.
    accepted = re.compile(r"(?:video\.[A-Za-z0-9._-]+|prompt\.txt|workflow\.json|comfy-request\.json"
                          r"|generate\.log|media\.json|prompt-rewrite\.json|input-conditioning\.pt"
                          r"|continuation-source\.safetensors|frame-preview\.jpg|(?:first|last)\.png"
                          r"|reference-\d{2}\.(?:png|mp4|wav)|\.(?:share|editor-export)-[0-9a-f]{32}\.mp4)")
    for item, _ in entries:
        rel = item.relative_to(folder)
        if len(rel.parts)==1:
            if not accepted.fullmatch(rel.name):
                raise ValueError("В папке находятся неизвестные файлы")
        elif rel.parts[0]!="video.artifacts":
            raise ValueError("В папке находятся неизвестные вложенные файлы")
    for sub in folder.iterdir():
        if sub.is_dir() and sub.name != "video.artifacts":
            raise ValueError("В папке есть неизвестный каталог")
    return amount


def recycle_failed(folder, output, recycler):
    """Only a verified old failed generation, through an injected Recycle Bin API.

    Never call rmtree/unlink; if Windows refuses recycling, the folder stays.
    """
    amount=eligible_failed(folder,output)
    recycler(str(Path(folder)))
    return amount



def api_query(clean=False, ports=(8188, 8189, 8190)):
    """Ask the running FreeVideo to clean files via its OWN built-in safe API."""
    path = "/freevideo/library/working-files" + ("/clear" if clean else "")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    errors = []
    for port in ports:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{int(port)}{path}",
                                             method="POST" if clean else "GET")
            with opener.open(request, timeout=1.2) as response:
                if response.getcode()!=200:
                    raise ValueError("Не удалось получить подтверждение")
                raw=response.read(32_000)
                value=json.loads(raw)
                if isinstance(value,dict) and ("freed_bytes" in value if clean else "bytes" in value):
                    return dict(port=int(port), **value)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(f"{port}: {type(exc).__name__}")
    raise ConnectionError("Не удалось подключиться к FreeVideo по 127.0.0.1:8188–8190. "
                          "Открой FreeVideo или используй Setup → Storage → Clear up. " +
                          ", ".join(errors))
