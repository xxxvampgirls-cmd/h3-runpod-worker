"""Bounded, read-only discovery of a local FreeVideo/ComfyUI custom_nodes folder."""
import os
import sys
from pathlib import Path

BRANCH_NAMES = ("ComfyUI", "comfyui", "ComfyUI_windows_portable",
                "FreeVideo", "FreeVideo-engine", "freevideo")
TARGET_NAME = "custom_nodes"


def _ordinary_directory(path):
    try:
        p = Path(path)
        # Reject parent junctions too, to avoid following them on Windows.
        for part in (p, *p.parents):
            info = os.lstat(part)
            if os.path.islink(part) or bool(getattr(info, "st_file_attributes", 0) & 0x400):
                return False
        return p.is_dir()
    except (OSError, ValueError, TypeError):
        return False


def _safe_directory_chain(path):
    """Do not accept a real custom_nodes reached through a linked ancestor.

    Windows junctions/NTFS reparse points may live *above* custom_nodes,
    while custom_nodes itself looks like a plain directory.
    """
    p=Path(path)
    for part in (p,*p.parents):
        if not _ordinary_directory(part):
            return False
    return True



def _direct_candidates(path):
    """A selected folder can be custom_nodes, ComfyUI, its parent or an output folder."""
    try:
        p = Path(path).expanduser()
    except (TypeError, ValueError, OSError):
        return []
    base = [p]
    base.extend(list(p.parents)[:5])
    result = []
    for parent in base:
        result.append(parent)
        result.append(parent / "custom_nodes")
        for name in BRANCH_NAMES:
            result.append(parent / name / "custom_nodes")
            result.append(parent / name / "ComfyUI" / "custom_nodes")
    return result


def normalize_custom_nodes(path):
    """Resolve only the selected location, never a neighbouring unrelated folder."""
    try:
        p = Path(path).expanduser()
    except (TypeError, ValueError, OSError):
        return None
    candidates = [p, p/"custom_nodes",
                  p/"ComfyUI"/"custom_nodes", p/"comfyui"/"custom_nodes",
                  p/"FreeVideo-engine"/"ComfyUI"/"custom_nodes",
                  p/"ComfyUI_windows_portable"/"ComfyUI"/"custom_nodes"]
    # Selecting output/FreeVideo or a run folder should resolve its ComfyUI.
    for parent in list(p.parents)[:5]:
        candidates.append(parent/"custom_nodes")
    for candidate in candidates:
        if candidate.name.casefold() == TARGET_NAME and _safe_directory_chain(candidate):
            return candidate
    return None


def discover_custom_nodes(hints=(), scan_roots=None):
    """Search known locations and likely install directories, not the user's whole disk.

    Root traversal is breadth-limited; excluded model/cache/output directories are never
    recursed into. No files are changed and no symlinks/junctions are followed.
    """
    found = {}

    def register(path):
        for candidate in _direct_candidates(path):
            if candidate.name.casefold() != TARGET_NAME or not _safe_directory_chain(candidate):
                continue
            try:
                key = os.path.normcase(os.path.abspath(str(candidate)))
            except (OSError, ValueError):
                continue
            if key not in found:
                found[key] = candidate

    for hint in hints:
        if hint:
            register(hint)

    if scan_roots is None:
        roots = [Path.home(), Path.cwd(), Path(sys.executable).parent]
        roots.extend(Path(os.environ[k]) for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE")
                     if os.environ.get(k))
        if os.name == "nt":
            roots.extend(Path(f"{letter}:\\") for letter in "MDC EFGH".replace(" ", ""))
    else:
        roots = [Path(p) for p in scan_roots]

    # Avoid probing megabytes of model directories or crawling user documents.
    queue = [(p, 0) for p in roots]
    visited = set()
    inspected = 0
    while queue and inspected < 360:
        folder, depth = queue.pop(0)
        try:
            key = os.path.normcase(os.path.abspath(str(folder)))
        except (OSError, ValueError):
            continue
        if key in visited or not _ordinary_directory(folder):
            continue
        visited.add(key)
        register(folder)
        inspected += 1
        if depth >= 3:
            continue
        try:
            children = list(folder.iterdir())[:160]
        except (OSError, PermissionError):
            continue
        for child in children:
            if not _ordinary_directory(child):
                continue
            name = child.name.casefold()
            if name in ("custom_nodes", "comfyui") or (
                "freevideo" in name or "comfyui" in name):
                register(child)
                queue.append((child, depth + 1))

    def rank(p):
        entries = tuple(x.name.casefold() for x in p.iterdir()) if _ordinary_directory(p) else ()
        freevideo = any(name == "freevideo" or name.startswith("freevideo-") for name in entries)
        bridge = "promptdock_freevideo_bridge" in entries
        return (not freevideo, not bridge, len(str(p)), str(p).casefold())

    return sorted(found.values(), key=rank)
