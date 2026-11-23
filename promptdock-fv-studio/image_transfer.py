"""Explicit file transfer for FreeVideo's saved original images.

Copy: original remains in place.
Move: first write and SHA256-verify a second file, then send old original
to the Windows Recycle Bin (no irreversible source unlink / shutil.move).
Never overwrite a destination. Never change FreeVideo workflow/requests.
"""
import hashlib
import os
import shutil
import stat
import time
from pathlib import Path

IMAGE_EXT={".jpg",".jpeg",".png",".webp",".bmp",".gif",".tif",".tiff"}
REPARSE=0x400

def _normal_file(path):
    try:
        info=os.lstat(path)
        return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not (
            getattr(info,"st_file_attributes",0)&REPARSE)
    except OSError:return False

def _normal_dir(path):
    try:
        info=os.lstat(path)
        return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not (
            getattr(info,"st_file_attributes",0)&REPARSE)
    except OSError:return False

def _safe_directory_chain(folder):
    for part in (Path(folder),*Path(folder).parents):
        if not _normal_dir(part):return False
    return True

def _hash(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            block=stream.read(1024*1024)
            if not block:break
            h.update(block)
    return h.hexdigest()

def transfer_image(src,destination,*,move=False,trash=None):
    """One image. Never overwrite, never remove the source irreversibly.

    If sending the source to Windows Recycle Bin fails, the verified copy is
    left in the destination and the operation is reported as COPY+warning.
    """
    source=Path(src).expanduser()
    folder=Path(destination).expanduser()
    if not _normal_file(source) or source.suffix.casefold() not in IMAGE_EXT:
        raise ValueError("Исходное изображение отсутствует или является ссылкой")
    if not _safe_directory_chain(source.parent):
        raise ValueError("Исходник расположен через ссылку или недоступную папку")
    if not _safe_directory_chain(folder):
        raise ValueError("Папка назначения не существует или является ссылкой/junction")
    if move and trash is None:
        raise ValueError("Перемещение разрешено только через Корзину Windows")
    if source.stat().st_size>2_000_000_000:
        raise ValueError("Изображение превышает допустимый размер")
    try:
        if folder.resolve(strict=True)==source.parent.resolve(strict=True):
            raise ValueError("Выбери другую папку: исходник уже находится здесь")
    except (OSError,RuntimeError):
        raise ValueError("Папка назначения недоступна")
    target=None
    # O_EXCL guarantees we never overwrite an existing user file, even if
    # the directory changed since the picker closed.
    try:
        for number in range(1,1001):
            candidate=(folder/source.name if number==1 else
                       folder/f"{source.stem} ({number}){source.suffix}")
            try:
                flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_BINARY",0)
                fd=os.open(candidate,flags,0o666)
                target=candidate
                break
            except FileExistsError:
                continue
        if target is None:
            raise ValueError("Не удалось подобрать свободное имя")
        before=source.stat()
        with os.fdopen(fd,"wb") as destination_stream,source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream,destination_stream,length=1024*1024)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
        shutil.copystat(source,target,follow_symlinks=False)
        if (source.stat().st_size!=before.st_size or
                source.stat().st_mtime_ns!=before.st_mtime_ns or
                _hash(source)!=_hash(target)):
            raise ValueError("Исходник изменился во время копирования; перенос отменён")
    except Exception:
        if target is not None:
            try:target.unlink()
            except OSError:pass
        raise
    moved=False
    warning=""
    if move:
        # Revalidate before trashing: a changed source is too risky.
        if not _normal_file(source) or _hash(source)!=_hash(target):
            warning="Исходник изменился. Копия создана, но исходный файл оставлен на месте."
        else:
            try:
                trash(str(source))
                moved=True
            except (OSError,ValueError,PermissionError) as exc:
                warning="Копия создана, но оригинал не удалось отправить в Корзину: "+str(exc)
    return {"original":str(source),"destination":str(target),
            "operation":"move" if moved else "copy",
            "requested_move":bool(move),"warning":warning,
            "at":int(time.time())}
