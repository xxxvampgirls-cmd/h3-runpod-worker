"""Read-only links to the actual source files used by a FreeVideo run.

Never infer a match by filename alone or copy original images. Only paths explicitly
recorded in a saved request/workflow are accepted. Missing paths remain visible.
"""
import json
import os
import re
import stat
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MEDIA_EXTS = IMAGE_EXTS | {".mp4", ".mov", ".webm", ".wav", ".mp3", ".flac"}
UUID_DIR = re.compile(r"^[a-fA-F0-9]{32}$")

def input_root_for_output(output):
    root = Path(output).expanduser()
    if root.name.lower() == "freevideo":
        root = root.parent
    if root.name.lower() != "output":
        return None
    return root.parent / "input"

def _unsafe(p):
    try:
        info = os.lstat(p)
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False
    except OSError:
        return True

def _resolve_input_path(candidate, input_root):
    if not isinstance(candidate, str) or not candidate or len(candidate) > 1300:
        return None
    candidate = candidate.strip()
    if "\x00" in candidate:
        return None
    normalized = candidate.replace("\\", "/")
    # Browser uploads are recorded relative to ComfyUI/input.
    if normalized.startswith("freevideo/"):
        parts = normalized.split("/")
        if len(parts) != 3 or not UUID_DIR.fullmatch(parts[1]):
            return None
        path = input_root / parts[0] / parts[1] / parts[2]
    else:
        # Accept other explicitly named media files under ComfyUI/input, but never
        # arbitrary files outside that trusted input root.
        p = Path(candidate)
        path = p if p.is_absolute() else input_root / p
    if path.suffix.lower() not in MEDIA_EXTS:
        return None
    try:
        base = input_root.resolve(strict=False)
        path = path.resolve(strict=False)
        if not path.is_relative_to(base) or path == base:
            return None
        if _unsafe(path) or any(_unsafe(parent) for parent in (path.parent,)):
            return None
        return str(path)
    except (OSError, RuntimeError, ValueError):
        return None

def linked_originals(output, *documents, input_override=None):
    """Only explicit saved asset references become links; never scan or guess uploads."""
    input_root = Path(input_override).expanduser() if input_override else input_root_for_output(output)
    if input_root is None:
        return []
    seen = set()
    results = []
    def add(value, role):
        path = _resolve_input_path(value, input_root)
        if not path or path.casefold() in seen:
            return
        seen.add(path.casefold())
        p = Path(path)
        results.append({"path": path, "name": p.name, "role": role,
                        "exists": p.is_file(), "kind": "изображение" if p.suffix.lower() in IMAGE_EXTS else "медиа"})
    def walk(value, role="", depth=0):
        if depth > 14 or len(results) > 50:
            return
        if isinstance(value, str):
            if len(value) < 200_000 and value.lstrip().startswith(("[", "{")):
                try: walk(json.loads(value), role, depth+1)
                except (ValueError, TypeError): pass
            else:
                add(value, role or "Референс")
        elif isinstance(value, list):
            for item in value[:80]: walk(item, role, depth+1)
        elif isinstance(value, dict):
            name = value.get("role") or value.get("kind") or role
            if not isinstance(name, str): name = role
            for key in ("file", "path", "filename"):
                if key in value:
                    walk(value[key], name, depth+1)
            # Narrow traversal to saved media/asset references and workflow nodes,
            # not arbitrary prompt text, LoRA model lists, output paths or logs.
            for key, child in value.items():
                lk = str(key).lower()
                if lk in ("file", "path", "filename", "prompt", "text", "loras", "lora", "adapter", "lora_details"):
                    continue
                if lk in ("first", "last", "middle", "references", "reference", "assets", "media",
                          "images", "input", "inputs", "widgets_values", "nodes", "workflow",
                          "data", "request", "original", "source", "source_files") or (
                          isinstance(child, (dict, list))):
                    walk(child, lk if lk in ("first", "last", "middle", "reference") else name, depth+1)
    for document in documents:
        walk(document)
    return results
