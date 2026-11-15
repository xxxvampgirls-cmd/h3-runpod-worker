"""PromptDock × FreeVideo Studio. Local-first prompt manager; never changes FreeVideo output.
Independent companion application; export/import PromptDock JSON v1 (+ galleryImages extension).
GPL-3.0-or-later.
"""
import base64, json, os, shutil, time, uuid, mimetypes, subprocess, re, stat, math, sys, threading
from send2trash import send2trash
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog as fd, messagebox as mb, simpledialog as sd
from PIL import Image, ImageTk
from ui_widgets import PillButton
from cleanup import inspect as inspect_storage, api_query, eligible_failed, root_dir

ROOT=Path(os.environ.get("LOCALAPPDATA") or Path.home())/"PromptDock-FreeVideo-Studio"
DB=ROOT/"library.json"
PHOTOS=ROOT/"images"
THUMBS=ROOT/"thumbnails"
BRIDGE_NAME="promptdock_freevideo_bridge"
def package_bridge_path():
    return Path(getattr(sys,"_MEIPASS",Path(__file__).parent))/"bridge"
PIC={".png",".jpg",".jpeg",".webp",".gif"}

def load_json(path, limit=8_000_000):
    try:
        p=Path(path)
        if p.is_symlink() or p.stat().st_size>limit: return {}
        v=json.loads(p.read_text(encoding="utf-8-sig"))
        return v if isinstance(v,dict) else {}
    except (ValueError,OSError,UnicodeError): return {}

def save_db(value,path=DB):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(path)

def _parse_lora_payload(value):
    """FreeVideo LoRA Stack saves adapters as a JSON *string* inside workflow widgets."""
    if isinstance(value, str) and value.strip().startswith(("[", "{")):
        try: return json.loads(value)
        except (ValueError, TypeError): return value
    return value

def _lora_name(value):
    if not isinstance(value, str) or not value.strip() or value in ("None", "null"):
        return ""
    # Full filesystem path from media.json; show only the filename, never an opaque path.
    return value.strip().replace("\\", "/").split("/")[-1]

def extract_loras(document, source):
    """Return actual or configured LoRAs in encounter order, with per-item strength.
    media.json is authoritative for *used* adapters; workflow is a saved configuration.
    """
    result = []
    def add(name, strength=None, enabled=True, alpha=None):
        name = _lora_name(name)
        if not name or not name.lower().endswith(".safetensors") or enabled is False:
            return
        if strength is not None and (type(strength) not in (int, float) or not math.isfinite(strength)):
            strength = None
        if strength == 0:
            return
        if alpha is not None and (type(alpha) not in (int, float) or not math.isfinite(alpha)):
            alpha = None
        result.append(dict(name=name, strength=strength, alpha=alpha, source=source))
    def walk(value, context="", depth=0):
        if depth > 16 or len(result) >= 64:
            return
        value = _parse_lora_payload(value)
        if isinstance(value, list):
            for row in value[:64]: walk(row, context, depth + 1)
        elif isinstance(value, dict):
            # Actual video request: media.json → loras → [{path, strength, alpha}, ...].
            if ("lora" in context or "adapter" in context) and ("path" in value or "name" in value):
                add(value.get("path") or value.get("name"), value.get("strength"),
                    value.get("enabled", True), value.get("alpha"))
                return
            cls = str(value.get("class_type") or value.get("type") or "").lower()
            if cls.endswith("freevideolora") and not cls.endswith("stack"):
                inputs = value.get("inputs") or {}
                if isinstance(inputs, dict):
                    add(inputs.get("lora"), inputs.get("strength", 1))
                widgets = value.get("widgets_values")
                if isinstance(widgets, list) and widgets:
                    add(widgets[0], widgets[1] if len(widgets)>1 else 1)
            if cls.endswith("freevideolorastack"):
                inputs = value.get("inputs") or {}
                if isinstance(inputs, dict): walk(inputs.get("adapters"), "adapters", depth+1)
                widgets = value.get("widgets_values")
                if isinstance(widgets, list):
                    for row in widgets: walk(row, "adapters", depth+1)
                elif isinstance(widgets, dict): walk(widgets.get("adapters"), "adapters", depth+1)
            for key, val in value.items():
                field = str(key).lower()
                if field in ("loras", "adapters", "lora_stack", "lora_list", "lora_models"):
                    walk(val, field, depth+1)
                elif isinstance(val, (dict, list)):
                    walk(val, field if ("lora" in field or "adapter" in field) else "", depth+1)
                elif field in ("loras", "adapters") and isinstance(val, str):
                    walk(val, field, depth+1)
                elif field in ("lora", "lora_name") and isinstance(val, str):
                    add(val, value.get("strength"), value.get("enabled", True))
    walk(document)
    unique=[];seen=set()
    for row in result:
        # Preserve distinct stacked entries with same name but different strength.
        signature=(row["name"].casefold(),row.get("strength"))
        if signature not in seen:
            seen.add(signature);unique.append(row)
    return unique[:32]

def read_lora_details(home, request, workflow):
    # FreeVideo writes the ACTUAL applied list, including strength, into media.json.
    # The workflow (including disabled LoRAs) describes configuration, not execution.
    media = load_json(home / "media.json", 4_000_000)
    applied = extract_loras(media, "Применена") if media else []
    configured = extract_loras(workflow, "В настройках") if workflow else []
    if applied:
        # Preserve workflow-only selections, but don't claim those were applied.
        signatures={row["name"].casefold() for row in applied}
        return applied + [row for row in configured if row["name"].casefold() not in signatures]
    if configured: return configured
    # Legacy files sometimes only keep a request in another JSON sidecar.
    for source in (request, load_json(home / "video.request.json", 4_000_000)):
        records=extract_loras(source, "В сохранённых данных")
        if records:return records
    return []


def from_freevideo(folder):
    root=Path(folder).expanduser()
    if root.name.casefold()!="freevideo":root=root/"FreeVideo"
    if not root.is_dir():return []
    rows=[]
    for video in root.glob("*/*/video.mp4"):
        try:
            if video.is_symlink() or video.parent.is_symlink() or video.stat().st_size==0:continue
            home=video.parent
            req=load_json(home/"comfy-request.json")
            if req.get("status") in ("starting","running","failed","cancelled"):continue
            p=home/"prompt.txt"
            prompt=p.read_text(encoding="utf-8-sig")[:80000] if p.is_file() and not p.is_symlink() else ""
            wf=load_json(home/"workflow.json")
            lora_details=read_lora_details(home,req,wf)
            preview=next((str(home/n) for n in ("video.thumbnail.jpg","frame-preview.jpg","first.png") if (home/n).is_file()),"")
            rows.append(dict(id="fv:"+home.relative_to(root).as_posix(),source="FreeVideo",
                title=(prompt.strip().splitlines()[0][:95] if prompt.strip() else home.name[:16]),
                prompt=prompt,video=str(video),preview=preview,
                seed=req.get("seed") if type(req.get("seed")) is int else None,
                geometry=req.get("geometry") if isinstance(req.get("geometry"),dict) else {},
                loras=[r["name"] for r in lora_details],loraDetails=lora_details,
                createdAt=int(video.stat().st_mtime*1000)))
        except (OSError,ValueError,UnicodeError,IndexError):continue
    return sorted(rows,key=lambda r:r["createdAt"],reverse=True)


def _is_reparse(path):
    """Reject symlinks and Windows junctions/reparse points."""
    try:
        info=os.lstat(path)
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except OSError: return True

def validate_deletion_folder(item, output):
    """Only a complete FreeVideo run at output/FreeVideo/YYYY-MM-DD/32hex."""
    if item.get("source") != "FreeVideo" or not item.get("video"):
        raise ValueError("Это не видео FreeVideo")
    root=Path(output).expanduser()
    if root.name.casefold() != "freevideo":root=root/"FreeVideo"
    if not root.is_dir() or _is_reparse(root):
        raise ValueError("Папка output не подключена или является ссылкой")
    root=root.resolve(strict=True)
    video=Path(item["video"])
    folder=video.parent
    if video.name!="video.mp4" or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",folder.parent.name):
        raise ValueError("Неизвестная структура папки FreeVideo")
    if not re.fullmatch(r"[0-9a-f]{32}",folder.name):
        raise ValueError("Неизвестный идентификатор генерации")
    if folder.parent.parent.resolve(strict=True)!=root or _is_reparse(folder.parent) or _is_reparse(folder) or _is_reparse(video):
        raise ValueError("Путь выходит за пределы output или содержит ссылку")
    if not video.is_file() or not video.stat().st_size:
        raise ValueError("Видео отсутствует или ещё записывается")
    request=load_json(folder/"comfy-request.json")
    if request.get("status")!="complete":
        raise ValueError("Генерация не подтверждена как завершённая")
    # Walking *without* following symlinks, reject Windows junctions anywhere
    # so deletion can never follow a nested link into unrelated data.
    total=0
    for dirpath, dirs, files in os.walk(folder, followlinks=False):
        for filename in dirs+files:
            entry=Path(dirpath)/filename
            if _is_reparse(entry):
                raise ValueError("В папке обнаружена ссылка или точка подключения")
            if entry.is_file():total+=entry.stat().st_size
    return folder,total



def build_preview(video):
    """Create a cached 1-second still with a bundled CPU ffmpeg.
    Never writes into FreeVideo output; only uses the companion's cache.
    """
    video=Path(video)
    if not video.is_file():return None
    try:
        import imageio_ffmpeg
        THUMBS.mkdir(parents=True,exist_ok=True)
        signature=f"{video.resolve()}:{video.stat().st_size}:{video.stat().st_mtime_ns}"
        import hashlib
        digest=hashlib.sha256(signature.encode("utf-8")).hexdigest()[:28]
        dest=THUMBS/(digest+".jpg")
        if dest.is_file() and dest.stat().st_size>500:return str(dest)
        temp=THUMBS/(digest+".tmp.jpg")
        args=[imageio_ffmpeg.get_ffmpeg_exe(),"-nostdin","-hide_banner","-loglevel","error",
              "-ss","0.8","-i",str(video),"-frames:v","1","-vf",
              "scale='min(600,iw)':-2", "-q:v","4","-y",str(temp)]
        flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
        result=subprocess.run(args,capture_output=True,timeout=20,creationflags=flags)
        if result.returncode==0 and temp.is_file() and temp.stat().st_size>500:
            temp.replace(dest)
            return str(dest)
        temp.unlink(missing_ok=True)
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):
        pass
    return None

def pics(row):
    out=[]
    for item in [row.get("preview",""),row.get("generatedPreview",""),*row.get("images",[])]:
        if not item:continue
        p=Path(item)
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in PIC and p not in out:out.append(p)
    return out

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        ROOT.mkdir(parents=True,exist_ok=True)
        self.db=load_json(DB,30_000_000)
        self.db.setdefault("items",{})
        if not isinstance(self.db["items"],dict):self.db["items"]={}
        self.selected=None;self.group="Все";self.keep=[];self.gallery=[];self.preview_image=None;self.preview_requested=set()
        self.disk_report=None;self.disk_scanning=False;self.disk_window=None
        self.title("PromptDock × FreeVideo Studio  |  Windows")
        self.geometry("1470x870");self.minsize(1090,710)
        self.configure(bg="#0c121f")
        st=ttk.Style();st.theme_use("clam")
        st.configure("Treeview",background="#182437",foreground="#ecf4ff",fieldbackground="#182437",rowheight=38,font=("Segoe UI",10),borderwidth=0)
        st.map("Treeview",background=[("selected","#3d5d97")],foreground=[("selected","#ffffff")])
        st.configure("Treeview.Heading",background="#253651",foreground="#b9d4f2",relief="flat",font=("Segoe UI",10,"bold"),padding=8)
        st.configure("TEntry",fieldbackground="#e6f0ff",foreground="#15263a",padding=5)
        st.configure("Lora.Treeview",rowheight=25,font=("Segoe UI",9),background="#132033",fieldbackground="#132033")
        st.configure("Lora.Treeview.Heading",font=("Segoe UI",9,"bold"),background="#23344e",foreground="#cce5fa")
        self.build();self.fill()
        self.protocol("WM_DELETE_WINDOW",self.quit_app)
        self.bind("<Control-s>",lambda e:self.save())
        if self.db.get("output"):self.after(400,self.scan)
        # Read-only background refresh: discover new FreeVideo outputs without a daemon.
        self.after(120000,self.auto_scan)
        self.after(30000,self.periodic_storage_audit)

    def btn(self,parent,label,cmd,primary=False,palette=None):
        return PillButton(parent,text=label,command=cmd,primary=primary,palette=palette)

    def build(self):
        hero=tk.Frame(self,bg="#101a2b",pady=13,padx=18)
        hero.pack(fill="x",padx=8,pady=(8,0))
        tk.Label(hero,text="◈  PROMPTDOCK",bg="#101a2b",fg="#f6f9ff",font=("Segoe UI",18,"bold")).pack(side="left")
        tk.Label(hero,text="×  FREEVIDEO  STUDIO",bg="#101a2b",fg="#76d3ff",font=("Segoe UI",15,"bold")).pack(side="left",padx=10)
        self.disk_hint=tk.Label(hero,text="●  DISK SCAN READY",bg="#17313C",fg="#9AF1E3",
                 font=("Segoe UI",9,"bold"),padx=12,pady=8)
        self.disk_hint.pack(side="right",padx=(0,8))
        tk.Label(hero,text="LOCAL / OFFLINE",bg="#25203C",fg="#CDBEFF",
                 font=("Segoe UI",9,"bold"),padx=12,pady=8).pack(side="right",padx=(0,9))
        tk.Frame(self,bg="#7660E5",height=3).pack(fill="x",padx=8)
        top=tk.Frame(self,bg="#121e30",pady=9,padx=12);top.pack(fill="x",padx=8)
        self.output_hint=tk.Label(top,text="Не подключена папка output",bg="#121e30",fg="#92b2d0",
                                  font=("Segoe UI",9),anchor="w")

        for name,cmd in [("📂 output",self.pick_output),("⟳ Обновить",self.scan),
            ("＋ Промпт",self.new),("⬆ Импорт JSON",self.import_json),
            ("⬇ Экспорт в PromptDock",self.export_json),("LoRA",self.lora_window),
            ("⇄ Подключить FreeVideo",self.install_bridge),("Очистка",self.open_storage)]:
            self.btn(top,name,cmd,name=="📂 output").pack(side="left",padx=3)
        self.search=tk.StringVar()
        ttk.Entry(top,textvariable=self.search,width=20).pack(side="right",padx=5)
        tk.Label(top,text="⌕ Поиск",bg="#121e30",fg="#bad4f4",font=("Segoe UI",10)).pack(side="right")
        self.output_hint.pack(side="bottom",fill="x",pady=(11,0))
        if self.db.get("output"):self.output_hint.config(text="◉  Каталог: "+str(self.db["output"]))
        self.search.trace_add("write",lambda *_:self.fill())
        panel=tk.PanedWindow(self,orient="horizontal",bg="#0c121f",sashwidth=8,bd=0,relief="flat")
        panel.pack(fill="both",expand=True,padx=9,pady=9)
        left=tk.Frame(panel,bg="#16243a",width=205);panel.add(left,minsize=165)
        tk.Label(left,text="  БИБЛИОТЕКА / ПАПКИ",bg="#16243a",fg="#b6cdf0",pady=14,font=("Segoe UI",10,"bold")).pack(anchor="w")
        self.groups=tk.Listbox(left,bg="#16243a",fg="#e4efff",selectbackground="#486aae",
            selectforeground="white",font=("Segoe UI",11),relief="flat",highlightthickness=0,activestyle="none",selectborderwidth=0)
        self.groups.pack(fill="both",expand=True,padx=5,pady=3)
        self.groups.bind("<<ListboxSelect>>",self.choose_group)
        self.btn(left,"⊞  Корзина промптов",self.open_prompt_trash).pack(side="bottom",fill="x",padx=10,pady=10)
        self.btn(left,"✕  Удалить мой промпт",self.delete_my_prompts,palette="danger").pack(side="bottom",fill="x",padx=10,pady=(5,0))
        middle=tk.Frame(panel,bg="#182539",width=375);panel.add(middle,minsize=245)
        tk.Label(middle,text="  КАТАЛОГ ГЕНЕРАЦИЙ",bg="#182539",fg="#c7defa",
            pady=14,font=("Segoe UI",10,"bold")).pack(fill="x",anchor="w")
        scroll=ttk.Scrollbar(middle)
        self.tree=ttk.Treeview(middle,columns=("src",),show="tree headings",selectmode="extended",yscrollcommand=scroll.set)
        scroll.config(command=self.tree.yview)
        self.tree.heading("#0",text="Название");self.tree.heading("src",text="Источник")
        self.tree.column("#0",width=240,minwidth=100)
        self.tree.column("src",width=95,minwidth=60,stretch=False)
        self.tree.bind("<<TreeviewSelect>>",self.choose_item)
        deletion=tk.Frame(middle,bg="#182539")
        deletion.pack(fill="x",side="bottom",padx=6,pady=7)
        tk.Label(middle,text="Ctrl / Shift — выбрать несколько видео",bg="#182539",fg="#8daacb",
                 font=("Segoe UI",9)).pack(fill="x",side="bottom",pady=3)
        self.tree.pack(side="left",fill="both",expand=True)
        scroll.pack(side="right",fill="y")
        self.btn(deletion,"Видео → Корзина",lambda:self.delete_selected(False)).pack(side="left",fill="x",expand=True,padx=2)
        self.btn(deletion,"Видео навсегда",lambda:self.delete_selected(True),palette="danger").pack(side="left",fill="x",expand=True,padx=2)
        workspace=tk.Frame(panel,bg="#19283c",padx=8,pady=8);panel.add(workspace,minsize=520)
        preview_side=tk.Frame(workspace,bg="#122238",width=255)
        preview_side.pack(side="right",fill="y",padx=(9,0))
        preview_side.pack_propagate(False)
        right=tk.Frame(workspace,bg="#19283c",padx=6,pady=6)
        right.pack(side="left",fill="both",expand=True)
        tk.Label(right,text="   РЕДАКТОР ПРОМПТА",fg="#b7d3f4",bg="#19283c",
                 font=("Segoe UI",10,"bold")).pack(fill="x",anchor="w",pady=(0,9))
        line=tk.Frame(right,bg="#19283c");line.pack(fill="x")
        self.titlevar=tk.StringVar()
        ttk.Entry(line,textvariable=self.titlevar,font=("Segoe UI",12)).pack(side="left",fill="x",expand=True)
        self.star=self.btn(line,"☆",self.favorite);self.star.pack(side="left",padx=5)
        self.btn(line,"Сохранить",self.save,True).pack(side="left")
        self.btn(line,"✕",self.delete_my_prompts,palette="danger").pack(side="left",padx=(4,0))
        row=tk.Frame(right,bg="#19283c",pady=8);row.pack(fill="x")
        tk.Label(row,text="Категория",bg="#19283c",fg="#c4d6f0").pack(side="left")
        self.catvar=tk.StringVar();ttk.Entry(row,textvariable=self.catvar,width=23).pack(side="left",padx=6)
        tk.Label(row,text="Теги",bg="#19283c",fg="#c4d6f0").pack(side="left")
        self.tagvar=tk.StringVar();ttk.Entry(row,textvariable=self.tagvar).pack(side="left",fill="x",expand=True,padx=6)
        tk.Label(right,text="ТЕКСТ  •  Можно менять без изменения оригиналов FreeVideo",bg="#19283c",fg="#9cbae2",anchor="w",font=("Segoe UI",9)).pack(fill="x",pady=6)
        self.body=tk.Text(right,wrap="word",height=10,bg="#101c2d",fg="#f1f6ff",
            insertbackground="white",relief="flat",font=("Consolas",10),padx=9,pady=9)
        self.body.pack(fill="both",expand=True)
        apply_row=tk.Frame(right,bg="#19283c",pady=6)
        apply_row.pack(fill="x",before=self.body)
        self.apply_text=tk.BooleanVar(value=True)
        self.apply_loras=tk.BooleanVar(value=True)
        self.apply_seed=tk.BooleanVar(value=True)
        self.apply_size=tk.BooleanVar(value=False)
        for label,state in [("Текст",self.apply_text),("LoRA",self.apply_loras),
                            ("Seed",self.apply_seed),("Размер/время",self.apply_size)]:
            tk.Checkbutton(apply_row,text=label,variable=state,bg="#19283c",fg="#c8d9f2",
                           selectcolor="#294768",activebackground="#19283c",activeforeground="#ffffff",
                           font=("Segoe UI",9)).pack(side="left",padx=2)
        self.btn(apply_row,"⇢ Применить в FreeVideo",self.apply_to_freevideo,True).pack(side="right",padx=4)
        controls=tk.Frame(right,bg="#19283c",pady=8);controls.pack(fill="x")
        for label,cmd in [("📋 Копировать",self.copy),("▶ Видео",self.video),
            ("📂 Папка",self.open_folder),("⧉ Дубликат",self.duplicate)]:
            self.btn(controls,label,cmd).pack(side="left",padx=3)
        self.info=tk.Label(right,text="",bg="#19283c",fg="#afd2f5",justify="left",anchor="w",
            wraplength=620,font=("Segoe UI",10))
        self.info.pack(fill="x",pady=7)
        lora_card=tk.Frame(right,bg="#142136",padx=8,pady=7,highlightbackground="#304660",highlightthickness=1)
        lora_card.pack(fill="x",pady=(6,3))
        header=tk.Frame(lora_card,bg="#142136");header.pack(fill="x",pady=(0,4))
        tk.Label(header,text="◈  LoRA  /  ПАРАМЕТРЫ ГЕНЕРАЦИИ",bg="#142136",fg="#8bdcff",
                 font=("Segoe UI",10,"bold")).pack(side="left")
        self.lora_count=tk.Label(header,text="0 адаптеров",bg="#243a57",fg="#d4edff",
                 font=("Segoe UI",9),padx=9,pady=3)
        self.lora_count.pack(side="right")
        self.lora_tree=ttk.Treeview(lora_card,columns=("strength","alpha","state"),
                show="tree headings",height=5,style="Lora.Treeview",selectmode="extended")
        self.lora_tree.heading("#0",text="Файл LoRA")
        self.lora_tree.heading("strength",text="Strength")
        self.lora_tree.heading("alpha",text="Alpha")
        self.lora_tree.heading("state",text="Источник")
        self.lora_tree.column("#0",width=300,minwidth=110)
        self.lora_tree.column("strength",width=80,minwidth=65,stretch=False,anchor="center")
        self.lora_tree.column("alpha",width=70,minwidth=50,stretch=False,anchor="center")
        self.lora_tree.column("state",width=110,minwidth=90,stretch=False,anchor="center")
        lora_scroll=ttk.Scrollbar(lora_card,command=self.lora_tree.yview)
        self.lora_tree.configure(yscrollcommand=lora_scroll.set)
        self.lora_tree.pack(side="left",fill="both",expand=True)
        lora_scroll.pack(side="right",fill="y")
        tk.Label(lora_card,text="Выдели несколько LoRA через Ctrl / Shift; без выделения применяются все.",
            bg="#142136",fg="#8daccd",font=("Segoe UI",8)).pack(side="bottom",anchor="w")

        # Sidebar: preview stays visible even when the text editor becomes short.
        gallery=tk.Frame(preview_side,bg="#122238",padx=8,pady=9,highlightbackground="#335371",highlightthickness=1)
        gallery.pack(fill="both",expand=True)
        tk.Label(gallery,text="◈  ПРЕВЬЮ",bg="#122238",fg="#95ddff",
            font=("Segoe UI",11,"bold")).pack(anchor="w",pady=(0,8))
        self.preview_label=tk.Label(gallery,text="Выбери видео\nдля просмотра",
            bg="#081320",fg="#7f9ebf",font=("Segoe UI",10),width=26,height=9,
            cursor="hand2",relief="flat")
        self.preview_label.pack(fill="x",pady=3)
        self.preview_label.bind("<Button-1>",lambda e:self.zoom(0) if self.gallery else self.video())
        self.btn(gallery,"▶ Открыть видео",self.video,True).pack(fill="x",pady=3)
        tk.Label(gallery,text="МИНИ-ГАЛЕРЕЯ",bg="#122238",fg="#b5ccf2",
            font=("Segoe UI",9,"bold")).pack(anchor="w",pady=(7,3))
        self.frames=tk.Frame(gallery,bg="#122238",height=152)
        self.frames.pack(fill="x")
        self.frames.pack_propagate(False)
        bar=tk.Frame(gallery,bg="#122238")
        bar.pack(fill="x",side="bottom",pady=6)
        self.btn(bar,"＋ Фото",self.add_photo).pack(fill="x",pady=(0,5))
        self.btn(bar,"Удалить фото",self.del_photo).pack(fill="x")
        self.status=tk.Label(self,text="●  STUDIO READY    •    FREEVIDEO / PROMPTS / LoRA / STORAGE",
            bg="#121e30",fg="#a9cfed",anchor="w",padx=16,pady=10,font=("Segoe UI",10))
        self.status.pack(fill="x")

    def persist(self):save_db(self.db)
    def fill(self):
        chosen=self.selected
        groups=["Все","★ Избранное","FreeVideo","Мои"]+sorted({v.get("category","") for v in self.db["items"].values() if v.get("category")})
        self.groups.delete(0,"end")
        for g in groups:self.groups.insert("end",g)
        try:self.groups.selection_set(groups.index(self.group))
        except ValueError:self.group="Все";self.groups.selection_set(0)
        self.tree.delete(*self.tree.get_children())
        self.tree.tag_configure("even",background="#192a42")
        self.tree.tag_configure("odd",background="#142236")
        q=self.search.get().strip().casefold()
        for pos,it in enumerate(sorted(self.db["items"].values(),key=lambda a:(not a.get("favorite",False),-a.get("createdAt",0)))):
            g=self.group
            if g=="★ Избранное" and not it.get("favorite"):continue
            if g=="FreeVideo" and it.get("source")!="FreeVideo":continue
            if g=="Мои" and it.get("source")=="FreeVideo":continue
            if g not in ("Все","★ Избранное","FreeVideo","Мои") and g!=it.get("category"):continue
            if q and q not in (" ".join([it.get("title",""),it.get("promptEdit",it.get("prompt","")),it.get("tags","")," ".join(it.get("loras",[]))])).casefold():continue
            name=("★ " if it.get("favorite") else "")+it.get("title","Без названия")
            self.tree.insert("",tk.END,iid=it["id"],text=name[:80],values=(it.get("source","Мой"),),tags=("odd" if pos%2 else "even",))
        if chosen and self.tree.exists(chosen):
            self.tree.selection_set(chosen);self.tree.see(chosen)

    def choose_group(self,e):
        selection=self.groups.curselection()
        if selection:
            g=self.groups.get(selection[0])
            if g!=self.group:self.group=g;self.fill()

    def choose_item(self,e):
        selection=self.tree.selection()
        if not selection:return
        if len(selection)!=1:return
        key=selection[0]
        if key==self.selected:return
        if self.selected:self.save(False)
        self.selected=key;it=self.db["items"].get(key)
        if not it:return
        self.titlevar.set(it.get("title",""))
        self.catvar.set(it.get("category",""))
        self.tagvar.set(it.get("tags",""))
        self.body.delete("1.0",tk.END)
        self.body.insert("1.0",it.get("promptEdit",it.get("prompt","")))
        self.star.config(text="★" if it.get("favorite") else "☆")
        geo=it.get("geometry") or {}
        self.info.config(text=f"Источник: {it.get('source','Мой')}    ◇ Seed: {it.get('seed','—')}    "+
           f"Видео: {geo.get('width','?')} × {geo.get('height','?')}    {geo.get('seconds','?')} сек.")
        self.show_lora_details(it)
        self.show_gallery()

    def show_lora_details(self, item):
        self.lora_tree.delete(*self.lora_tree.get_children())
        rows=item.get("loraDetails",[])
        if not rows and item.get("loras"):
            rows=[{"name":s,"strength":None,"alpha":None,"source":"Старые данные"} for s in item["loras"]]
        self.lora_count.config(text=f"{len(rows)}  LoRA")
        if not rows:
            self.lora_tree.insert("",tk.END,text="В сохранённых файлах LoRA не найдены",values=("—","—","—"))
            return
        for row in rows:
            def num(v):
                if type(v) not in (int,float):return "—"
                return f"{v:g}"
            self.lora_tree.insert("",tk.END,iid=str(self.lora_tree.get_children().__len__()),text=row.get("name","—"),values=(
                num(row.get("strength")),num(row.get("alpha")),row.get("source","—")))

    def save(self,redraw=True):
        if not self.selected or self.selected not in self.db["items"]:return
        it=self.db["items"][self.selected]
        it.update(title=self.titlevar.get().strip()[:250] or "Без названия",
            category=self.catvar.get().strip()[:90] or "Без категории",
            tags=self.tagvar.get().strip()[:450],promptEdit=self.body.get("1.0","end-1c"))
        self.persist()
        if redraw:self.fill();self.status.config(text="Изменения сохранены")

    def favorite(self):
        if not self.selected:return
        it=self.db["items"][self.selected]
        it["favorite"]=not it.get("favorite",False)
        self.star.config(text="★" if it["favorite"] else "☆")
        self.save()

    def new(self):
        if self.selected:self.save(False)
        key="manual:"+str(uuid.uuid4())
        self.db["items"][key]=dict(id=key,title="Новый промпт",prompt="",source="Мой",
             category="Мои",tags="",favorite=False,images=[],createdAt=int(time.time()*1000))
        self.group="Все";self.persist();self.fill()
        self.selected=None;self.tree.selection_set(key);self.choose_item(None)

    def duplicate(self):
        if not self.selected:return
        self.save(False);it=dict(self.db["items"][self.selected])
        key="manual:"+str(uuid.uuid4())
        it.update(id=key,source="Мой",video="",title=it.get("title","") + " (копия)",createdAt=int(time.time()*1000))
        self.db["items"][key]=it;self.persist();self.group="Все";self.fill()
        self.selected=None;self.tree.selection_set(key);self.choose_item(None)

    def auto_scan(self):
        if self.db.get("output"):
            try: self.scan()
            except (OSError,ValueError): pass
        if self.winfo_exists():self.after(120000,self.auto_scan)

    def pick_output(self):
        path=fd.askdirectory(title="Выбери output или output/FreeVideo",initialdir=self.db.get("output") or str(Path.home()))
        if path:self.db["output"]=path;self.output_hint.config(text="◉  Каталог: "+path);self.persist();self.scan()

    def scan(self):
        folder=self.db.get("output")
        if not folder:self.pick_output();return
        rows=from_freevideo(folder);new=0
        for item in rows:
            key=item["id"]
            if key not in self.db["items"] and key in self.db.get("deletedHistory",{}):
                self.db["items"][key]=self.db["deletedHistory"].pop(key)
            if key in self.db["items"]:
                it=self.db["items"][key]
                it.update({name:item[name] for name in ("video","preview","seed","geometry","loras","loraDetails")})
                if not it.get("prompt"):it["prompt"]=item["prompt"]
            else:
                item.update(category="FreeVideo",tags="",favorite=False,images=[])
                self.db["items"][key]=item;new+=1
        self.persist();self.fill()
        if self.selected and self.selected in self.db["items"]:
            self.show_lora_details(self.db["items"][self.selected])
            self.show_gallery()
        self.status.config(text=f"◉ Проверено {len(rows)} видео  •  новых: {new}  •  оригиналы не изменены.")

    def bridge_folder(self):
        raw=self.db.get("bridgePath","")
        if not isinstance(raw,str) or not raw:return None
        p=Path(raw)
        return p if (p/"__init__.py").is_file() and (p/"web"/"bridge.js").is_file() else None

    def install_bridge(self):
        if not mb.askyesno("Локальный мост FreeVideo",
            "Установить маленькое дополнение в ComfyUI/custom_nodes?\n\n"
            "Оно получает только выбранный тобой промпт, LoRA и параметры, "
            "меняет открытый граф FreeVideo. Не запускает генерацию и не изменяет файлы видео.\n\n"
            "После установки потребуется перезапустить FreeVideo / ComfyUI.",
            parent=self):return
        initial=self.db.get("customNodes") or self.db.get("output") or str(Path.home())
        folder=fd.askdirectory(title="Выбери папку ComfyUI/custom_nodes",initialdir=initial,parent=self)
        if not folder:return
        node_dir=Path(folder)
        if node_dir.name.casefold()!="custom_nodes" and (node_dir/"custom_nodes").is_dir():
            node_dir=node_dir/"custom_nodes"
        if node_dir.name.casefold()!="custom_nodes" or not node_dir.is_dir():
            mb.showerror("Не найдена папка","Нужно выбрать настоящую папку ComfyUI/custom_nodes.",parent=self)
            return
        if _is_reparse(node_dir):
            mb.showerror("Установка","Папка является ссылкой или точкой подключения. Выбери обычную папку.",parent=self)
            return
        source=package_bridge_path()
        if not (source/"__init__.py").is_file() or not (source/"web"/"bridge.js").is_file():
            mb.showerror("Ошибка","В сборке отсутствуют файлы моста.",parent=self)
            return
        target=node_dir/BRIDGE_NAME
        if target.exists() and (not target.is_dir() or
           not (target/"__init__.py").is_file() or
           "PromptDock one-way local handoff" not in (target/"__init__.py").read_text(encoding="utf-8",errors="replace")):
            mb.showerror("Нельзя перезаписать","Папка назначения занята другим дополнением.",parent=self)
            return
        try:
            (target/"web").mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/"__init__.py",target/"__init__.py")
            shutil.copy2(source/"web"/"bridge.js",target/"web"/"bridge.js")
            (target/"queue").mkdir(exist_ok=True)
            self.db["customNodes"]=str(node_dir)
            self.db["bridgePath"]=str(target)
            self.persist()
            mb.showinfo("Мост установлен","Готово!\n\n1. Перезапусти FreeVideo / ComfyUI.\n"
              "2. Открой окно с графом FreeVideo.\n"
              "3. Выбери промпт в библиотеке и нажми «Применить в FreeVideo».\n\n"
              "После этого текст и параметры появятся в открытом графе.",parent=self)
            self.status.config(text="⇄ Мост установлен. Перезапусти FreeVideo / ComfyUI.")
        except (OSError,ValueError) as error:
            mb.showerror("Установка",str(error),parent=self)

    def apply_to_freevideo(self):
        if not self.selected or self.selected not in self.db["items"]:
            mb.showinfo("Применить","Сначала выбери один промпт.",parent=self)
            return
        target=self.bridge_folder()
        if not target:
            self.install_bridge()
            return
        self.save(False)
        item=self.db["items"][self.selected]
        include=[]
        if self.apply_loras.get():
            all_rows=item.get("loraDetails") or []
            ids=self.lora_tree.selection()
            if ids:
                try:all_rows=[all_rows[int(i)] for i in ids]
                except (IndexError,ValueError):pass
            # Prefer LoRAs actually used; keep workflow-only rows only if no actual list exists.
            elif any(row.get("source")=="Применена" for row in all_rows):
                all_rows=[row for row in all_rows if row.get("source")=="Применена"]
            for row in all_rows:
                name=row.get("name","")
                strength=row.get("strength")
                if isinstance(name,str) and name.lower().endswith(".safetensors") and type(strength) in (int,float):
                    if math.isfinite(strength) and strength!=0:
                        include.append(dict(name=name,strength=strength))
            if all_rows and len(include)<len(all_rows):
                mb.showwarning("Часть LoRA пропущена",
                    "Некоторые LoRA не имеют сохранённого значения Strength. "
                    "Они не будут применены — программа не придумывает параметры.",parent=self)
            if not all_rows and not mb.askyesno("LoRA не найдены",
               "В записи LoRA отсутствуют. Продолжить без изменения LoRA в FreeVideo?",parent=self):
                return
        g=item.get("geometry",{})
        geometry=({k:g[k] for k in ("width","height","seconds") if type(g.get(k)) in (int,float) and g[k]>0}
                  if self.apply_size.get() and isinstance(g,dict) else None)
        seed=item.get("seed") if self.apply_seed.get() and type(item.get("seed")) is int else None
        prompt=self.body.get("1.0","end-1c") if self.apply_text.get() else None
        if not any([prompt is not None,include,seed is not None,geometry]):
            mb.showinfo("Пустой выбор","Выбери хотя бы один параметр для применения.",parent=self)
            return
        what=[]
        if prompt is not None:what.append("текст")
        if include:what.append(f"LoRA: {len(include)}")
        if seed is not None:what.append(f"seed {seed}")
        if geometry:what.append("размер / время")
        if not mb.askyesno("Применить в FreeVideo",
              "Поменять параметры в ОТКРЫТОМ графе FreeVideo?\n\n"
              +", ".join(what)+"\n\nТекущие настройки могут быть заменены.\n"
              "Генерация НЕ запускается.",parent=self):return
        payload=dict(text=prompt,loras=include if include else None,seed=seed,geometry=geometry)
        queue=target/"queue"
        request=dict(id=uuid.uuid4().hex,expires=time.time()+90,payload=payload)
        try:
            queue.mkdir(exist_ok=True)
            draft=queue/"pending.tmp"
            draft.write_text(json.dumps(request,ensure_ascii=False),encoding="utf-8")
            draft.replace(queue/"pending.json")
            (queue/"ack.json").unlink(missing_ok=True)
            self.status.config(text="⇢ Ожидание FreeVideo… Открой окно FreeVideo в браузере.")
            self.wait_for_ack(request["id"],queue,0)
        except OSError as error:
            mb.showerror("Не удалось отправить",str(error),parent=self)

    def wait_for_ack(self,request_id,queue,attempt):
        reply=load_json(queue/"ack.json",50_000)
        if reply.get("id")==request_id:
            if reply.get("ok"):
                self.status.config(text="✓ "+reply.get("message","Настройки применены"))
                mb.showinfo("FreeVideo",reply.get("message","Настройки применены"),parent=self)
            else:
                self.status.config(text="Не удалось применить: "+reply.get("message",""))
                mb.showwarning("FreeVideo",reply.get("message","Неизвестная ошибка"),parent=self)
            return
        if attempt>=60:
            self.status.config(text="Мост не ответил. Проверь, запущен ли FreeVideo и установлен ли мост.")
            mb.showwarning("Мост не отвечает",
                "Открой FreeVideo после перезапуска ComfyUI и убедись, что есть узел генерации.\n"
                "Запрос истечёт автоматически; генерация не запущена.",parent=self)
            return
        self.after(1500,lambda:self.wait_for_ack(request_id,queue,attempt+1))

    def copy(self):
        if not self.selected:return
        self.clipboard_clear();self.clipboard_append(self.body.get("1.0","end-1c"))
        self.status.config(text="Промпт скопирован")

    def video(self):
        if not self.selected:return
        p=Path(self.db["items"][self.selected].get("video") or "")
        if not p.is_file():mb.showinfo("Видео","Видео для этого промпта не найдено");return
        if os.name=="nt":os.startfile(str(p))
        else:subprocess.Popen(["xdg-open",str(p)])

    def open_folder(self):
        if not self.selected:return
        p=Path(self.db["items"][self.selected].get("video") or "")
        if not p.is_file():return
        if os.name=="nt":subprocess.Popen(["explorer",'/select,'+str(p)])
        else:subprocess.Popen(["xdg-open",str(p.parent)])

    def delete_my_prompts(self):
        """Move imported and manually created prompts to a reversible local trash."""
        picked=list(self.tree.selection())
        if not picked and self.selected:picked=[self.selected]
        owned=[key for key in picked if key in self.db["items"] and
               self.db["items"][key].get("source")!="FreeVideo"]
        if not owned:
            mb.showinfo("Удаление промпта",
                "Выбери созданный вручную или импортированный промпт.\n"
                "Для видео FreeVideo используй отдельные кнопки «Видео → Корзина».",
                parent=self)
            return
        if self.selected:self.save(False)
        titles=[self.db["items"][key].get("title","Без названия") for key in owned]
        if not mb.askyesno("Удалить мои промпты",
            f"Убрать из библиотеки {len(owned)} промпт(а/ов)?\n\n"
            +"\n".join("• "+title[:95] for title in titles[:6])
            +("\n…" if len(titles)>6 else "")
            +"\n\nЗаписи можно восстановить через «Корзина промптов».\n"
             "Оригинальные видео FreeVideo не будут затронуты.",parent=self,icon="warning"):
            return
        trash=self.db.setdefault("promptTrash",{})
        for key in owned:
            trash[key]={"entry":self.db["items"].pop(key),"deletedAt":int(time.time())}
        if self.selected in owned:
            self.clear_editor()
        self.persist()
        self.fill()
        self.status.config(text=f"✓  {len(owned)} своих промптов перемещено в корзину библиотеки.")

    def clear_editor(self):
        self.selected=None
        self.titlevar.set("")
        self.catvar.set("")
        self.tagvar.set("")
        self.body.delete("1.0",tk.END)
        self.info.config(text="")
        self.show_lora_details({})
        self.show_gallery()

    def open_prompt_trash(self):
        archive=self.db.setdefault("promptTrash",{})
        win=tk.Toplevel(self,bg="#111B2C")
        win.title("Корзина промптов")
        win.geometry("660x460")
        tk.Label(win,text="КОРЗИНА ПРОМПТОВ",font=("Segoe UI",16,"bold"),
                 bg="#111B2C",fg="#ECEFFF",pady=16).pack(fill="x")
        tk.Label(win,text="Тут только твои промпты; видео FreeVideo не меняются.",
                 font=("Segoe UI",10),bg="#111B2C",fg="#A6BCD9").pack(pady=(0,12))
        table=ttk.Treeview(win,columns=("source",),show="tree headings",selectmode="extended")
        table.heading("#0",text="Промпт")
        table.heading("source",text="Источник")
        table.column("#0",width=450)
        table.column("source",width=130)
        table.pack(fill="both",expand=True,padx=17,pady=8)
        def refill():
            table.delete(*table.get_children())
            for key,rec in sorted(archive.items(),key=lambda x:-x[1].get("deletedAt",0)):
                item=rec.get("entry",{})
                table.insert("",tk.END,iid=key,text=item.get("title","Без названия"),
                             values=(item.get("source","Мой"),))
        def restore():
            keys=list(table.selection())
            for key in keys:
                if key not in archive:continue
                item=archive.pop(key)["entry"]
                self.db["items"][key]=item
            self.persist();self.group="Все";self.fill();refill()
            self.status.config(text=f"✓  Восстановлено промптов: {len(keys)}")
        def forget():
            keys=list(table.selection())
            if not keys:return
            if not mb.askyesno("Удалить безвозвратно",
                f"Удалить {len(keys)} промптов из корзины навсегда?\n"
                "Данные заметок и связей с фото восстановить больше нельзя.\n"
                "Сами фото, если есть, останутся в локальном хранилище.",
                parent=win,icon="warning"):return
            for key in keys:archive.pop(key,None)
            self.persist();refill()
        bar=tk.Frame(win,bg="#111B2C",pady=14)
        bar.pack(fill="x",padx=15)
        self.btn(bar,"↶  Восстановить",restore,True).pack(side="left",padx=5)
        self.btn(bar,"✕  Очистить выбранное",forget,palette="danger").pack(side="left",padx=5)
        self.btn(bar,"Закрыть",win.destroy).pack(side="right",padx=5)
        refill()

    def delete_selected(self, permanent=False):
        selected=list(self.tree.selection())
        if not selected and self.selected:selected=[self.selected]
        if not selected:return
        self.save(False)
        records=[]
        errors=[]
        for key in selected:
            item=self.db["items"].get(key,{})
            try:
                folder,size=validate_deletion_folder(item,self.db.get("output",""))
                records.append((key,folder,size))
            except (OSError,ValueError) as error:
                errors.append((item.get("title",key),str(error)))
        if not records:
            mb.showwarning("Удаление недоступно","Выбери завершённые видео FreeVideo.\n"+
                           "\n".join(f"{name[:40]}: {reason}" for name,reason in errors[:6]))
            return
        total=sum(r[2] for r in records)
        folders=len(records)
        capacity=f"{total / (1024**3):.2f} ГБ" if total>=1024**3 else f"{total / (1024**2):.1f} МБ"
        head=(f"Будет удалено папок: {folders}\nОбщий объём: {capacity}\n"
              "Удаляются целые папки генераций, включая MP4, превью и метаданные.\n"
              "Исходные файлы FreeVideo других генераций не изменятся.")
        if errors:head+=f"\nПропущено защищённых или неподходящих: {len(errors)}."
        if permanent:
            head+="\n\nВНИМАНИЕ: восстановление через Корзину будет невозможно."
            if not mb.askyesno("Безвозвратное удаление",head,parent=self,icon="warning"):return
            typed=sd.askstring("Подтверждение",f"Для безвозвратного удаления {folders} папок введи слово УДАЛИТЬ:",parent=self)
            if typed!="УДАЛИТЬ":return
        elif not mb.askyesno("Переместить в Корзину",head+"\n\nПереместить в Корзину Windows?",
                              parent=self,icon="warning"):return
        ok=0;failed=[]
        for key,folder,_ in records:
            try:
                # Revalidate immediately before writing to reduce accidental deletions.
                check,_=validate_deletion_folder(self.db["items"][key],self.db.get("output",""))
                if check!=folder:raise ValueError("Путь изменился")
                if permanent:shutil.rmtree(folder)
                else:send2trash(str(folder))
                # Keep notes for potential recovery after restoring from Recycle Bin.
                self.db.setdefault("deletedHistory",{})[key]=self.db["items"][key]
                del self.db["items"][key]
                ok+=1
            except (OSError,ValueError) as error:failed.append(f"{folder.name}: {error}")
        if self.selected not in self.db["items"]:
            self.selected=None
            self.titlevar.set("");self.catvar.set("");self.tagvar.set("")
            self.body.delete("1.0",tk.END)
            self.info.config(text="")
            self.show_lora_details({})
            self.show_gallery()
        self.persist();self.fill()
        result=f"Удалено папок: {ok}/{folders}. Выбранный объём: {capacity}."
        if failed:result+="\nНе удалось:\n"+"\n".join(failed[:8])
        mb.showinfo("Удаление завершено",result)
        self.status.config(text=result.split("\n")[0])

    def show_gallery(self):
        for child in self.frames.winfo_children():child.destroy()
        self.keep=[];self.gallery=[];self.preview_image=None
        self.preview_label.configure(image="",text="Нет изображения",width=26,height=9)
        if not self.selected:return
        item=self.db["items"][self.selected]
        self.gallery=pics(item)
        if self.gallery:
            self.display_large_preview(self.gallery[0])
        elif item.get("video"):
            video=item["video"]
            if video not in self.preview_requested:
                self.preview_requested.add(video)
                self.preview_label.configure(text="Загружается\nкадр из видео…")
                def task():
                    result=build_preview(video)
                    def complete():
                        self.preview_requested.discard(video)
                        if not self.selected or self.db["items"].get(self.selected,{}).get("video")!=video:return
                        if result and Path(result).is_file():
                            self.db["items"][self.selected]["generatedPreview"]=result
                            self.show_gallery()
                        else:self.preview_label.configure(text="Превью не найдено\n▶ Открыть видео")
                    try:self.after(0,complete)
                    except RuntimeError:pass
                threading.Thread(target=task,daemon=True).start()
            else:self.preview_label.configure(text="Загружается\nкадр из видео…")
        all_pics=list(self.gallery)
        for i,p in enumerate(all_pics[:4]):
            try:
                with Image.open(p) as im:
                    im.thumbnail((100,64));photo=ImageTk.PhotoImage(im.convert("RGB"))
                self.keep.append(photo)
                tk.Button(self.frames,image=photo,relief="flat",bg="#122238",cursor="hand2",
                    command=lambda j=i:self.zoom(j)).grid(row=i//2,column=i%2,padx=3,pady=3)
            except (ValueError,OSError):pass
        if len(all_pics)>4:
            self.btn(self.frames,f"Ещё: {len(all_pics)-4}",lambda:self.zoom(4)).grid(row=2,column=0,columnspan=2)

    def display_large_preview(self, image_path):
        try:
            with Image.open(image_path) as im:
                im.thumbnail((235,180))
                self.preview_image=ImageTk.PhotoImage(im.convert("RGB"))
            self.preview_label.configure(image=self.preview_image,text="",height=180,width=235)
        except (ValueError,OSError):
            self.preview_label.configure(image="",text="Превью повреждено")



    def zoom(self,index):
        if not self.gallery:return
        win=tk.Toplevel(self);win.title("Галерея промпта")
        win.geometry("1040x750");win.configure(bg="#0e1929")
        label=tk.Label(win,bg="#0e1929");label.pack(fill="both",expand=True)
        caption=tk.Label(win,bg="#0e1929",fg="white");caption.pack(pady=6)
        pos=[index]
        def show(index):
            pos[0]=index%len(self.gallery);p=self.gallery[pos[0]]
            try:
                with Image.open(p) as im:
                    im.thumbnail((1000,640));ref=ImageTk.PhotoImage(im.convert("RGB"))
                label.config(image=ref);label.image=ref
            except (ValueError,OSError):pass
            caption.config(text=f"{pos[0]+1}/{len(self.gallery)}    {p.name}")
        buttons=tk.Frame(win,bg="#0e1929");buttons.pack(pady=7)
        self.btn(buttons,"◀ Назад",lambda:show(pos[0]-1)).pack(side="left",padx=9)
        self.btn(buttons,"Вперёд ▶",lambda:show(pos[0]+1)).pack(side="left",padx=9)
        win.bind("<Left>",lambda e:show(pos[0]-1))
        win.bind("<Right>",lambda e:show(pos[0]+1))
        show(index)

    def add_photo(self):
        if not self.selected:return
        paths=fd.askopenfilenames(title="Выбери фотографии",filetypes=[("Картинки","*.jpg *.jpeg *.png *.webp *.gif")])
        if not paths:return
        it=self.db["items"][self.selected];images=it.setdefault("images",[])
        folder=PHOTOS/str(uuid.uuid5(uuid.NAMESPACE_URL,self.selected));folder.mkdir(parents=True,exist_ok=True)
        added=0
        for name in paths:
            if len(images)>=39:break
            p=Path(name)
            try:
                if not p.is_file() or p.suffix.lower() not in PIC or p.stat().st_size>8_000_000:continue
                with Image.open(p) as im:im.verify()
                dest=folder/(uuid.uuid4().hex+p.suffix.lower())
                shutil.copy2(p,dest);images.append(str(dest));added+=1
            except (OSError,ValueError):pass
        self.persist();self.show_gallery();self.status.config(text=f"Добавлено фото: {added}")

    def del_photo(self):
        if not self.selected:return
        images=self.db["items"][self.selected].get("images",[])
        if not images:mb.showinfo("Фото","Добавленных фото нет. Превью FreeVideo не удаляем.");return
        i=sd.askinteger("Удалить",f"Номер добавленного фото: 1–{len(images)}",minvalue=1,maxvalue=len(images),parent=self)
        if i is None:return
        p=Path(images.pop(i-1))
        try:
            if p.resolve().is_relative_to(PHOTOS.resolve()):p.unlink(missing_ok=True)
        except OSError:pass
        self.persist();self.show_gallery()

    def lora_window(self):
        folder=fd.askdirectory(title="ComfyUI/models/loras",initialdir=self.db.get("loraRoot") or self.db.get("output") or str(Path.home()))
        if not folder:return
        self.db["loraRoot"]=folder;self.persist()
        paths=sorted((p for p in Path(folder).rglob("*.safetensors") if p.is_file() and not p.is_symlink()),key=lambda p:p.name.casefold())
        win=tk.Toplevel(self);win.title("LoRA на диске — не все совместимы с MiniMax H3")
        win.geometry("810x540")
        tk.Label(win,text=f"Найдено {len(paths)} файлов. Двойной щелчок копирует имя.",bg="#20334e",fg="white",pady=10).pack(fill="x")
        tree=ttk.Treeview(win,columns=("size",),show="tree headings")
        tree.heading("#0",text="Файл");tree.heading("size",text="Размер, МБ")
        tree.column("#0",width=620);tree.column("size",width=120)
        tree.pack(fill="both",expand=True)
        for i,p in enumerate(paths):
            tree.insert("",tk.END,iid=str(i),text=str(p.relative_to(folder)),values=(round(p.stat().st_size/1048576),))
        def copy(e):
            if tree.selection():
                p=paths[int(tree.selection()[0])];self.clipboard_clear();self.clipboard_append(p.name)
        tree.bind("<Double-1>",copy)

    def import_json(self):
        name=fd.askopenfilename(title="Открыть JSON PromptDock",filetypes=[("JSON","*.json")])
        if not name:return
        archive=load_json(name,50_000_000)
        rows=archive.get("prompts")
        if not isinstance(rows,list):mb.showerror("Ошибка","Не найден массив prompts");return
        added=0
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get("body"),str):continue
            key="dock:"+str(row.get("id") or uuid.uuid4())
            if key in self.db["items"]:continue
            self.db["items"][key]=dict(id=key,title=str(row.get("title") or "Без названия"),
                prompt=row["body"],category=str(row.get("folder") or "Мои"),
                tags=", ".join(row.get("tags",[]) if isinstance(row.get("tags"),list) else []),
                favorite=bool(row.get("favorite")),source="PromptDock",images=[],
                createdAt=int(row.get("createdAt") or time.time()*1000))
            added+=1
        # Import Gallery extension payloads (if present), matching original PromptDock IDs.
        # Never execute or follow paths from a JSON archive. Each image is decoded
        # only from verified inline data, and stored under this app's local directory.
        image_count=0
        images=archive.get("galleryImages",[])
        if isinstance(images,list):
            for image in images[:2000]:
                if not isinstance(image,dict):continue
                key="dock:"+str(image.get("promptId",""))
                if key not in self.db["items"]:continue
                collection=self.db["items"][key].setdefault("images",[])
                if len(collection)>=39:continue
                url=image.get("dataUrl","")
                if not isinstance(url,str) or len(url)>12_000_000 or not url.startswith("data:image/") or ";base64," not in url:continue
                header,_,raw=url.partition(",")
                mime=header[5:-7]
                suffix={"image/jpeg":".jpg","image/png":".png","image/webp":".webp","image/gif":".gif"}.get(mime)
                if suffix is None:continue
                try:
                    data=base64.b64decode(raw,validate=True)
                    if not 0<len(data)<=8*1024*1024:continue
                    directory=PHOTOS/str(uuid.uuid5(uuid.NAMESPACE_URL,key))
                    directory.mkdir(parents=True,exist_ok=True)
                    dest=directory/(uuid.uuid4().hex+suffix)
                    dest.write_bytes(data)
                    with Image.open(dest) as check:check.verify()
                    collection.append(str(dest));image_count+=1
                except (OSError,ValueError,base64.binascii.Error):
                    try:dest.unlink(missing_ok=True)
                    except (OSError,UnboundLocalError):pass
        self.persist();self.group="Все";self.fill()
        self.status.config(text=f"Импортировано из PromptDock: {added}, изображений: {image_count}")

    def export_json(self):
        if self.selected:self.save(False)
        target=fd.asksaveasfilename(title="Экспорт JSON PromptDock",defaultextension=".json",initialfile="promptdock-freevideo.json",filetypes=[("JSON","*.json")])
        if not target:return
        prompts=[];gallery=[]
        for item in self.db["items"].values():
            key=str(uuid.uuid5(uuid.NAMESPACE_URL,item["id"]))
            prompts.append(dict(id=key,title=item.get("title","Без названия"),
                body=item.get("promptEdit",item.get("prompt","")),
                tags=[v.strip() for v in item.get("tags","").split(",") if v.strip()],
                folder=item.get("category",""),favorite=bool(item.get("favorite")),
                pinned=False,useCount=0,lastUsedAt=None,
                createdAt=item.get("createdAt",int(time.time()*1000)),updatedAt=int(time.time()*1000)))
            for pos,p in enumerate(pics(item)[:40]):
                try:
                    if p.stat().st_size>8*1024*1024:continue
                    mime=mimetypes.guess_type(p.name)[0] or "image/jpeg"
                    if mime not in ("image/jpeg","image/png","image/webp","image/gif"):continue
                    gallery.append(dict(id=str(uuid.uuid4()),promptId=key,filename=p.name,
                        mime=mime,dataUrl="data:"+mime+";base64,"+base64.b64encode(p.read_bytes()).decode("ascii"),
                        sortOrder=pos))
                except OSError:pass
        save_db(dict(format="promptdock",version=1,exportedAt=int(time.time()*1000),
            prompts=prompts,galleryImages=gallery),Path(target))
        mb.showinfo("Экспорт",f"Готово: {len(prompts)} промптов и {len(gallery)} фото.\nТекст импортирует обычный PromptDock. Фото импортирует версия с Gallery-патчем.")

    def quit_app(self):
        if self.selected:self.save(False)
        self.destroy()

if __name__=="__main__":
    App().mainloop()
