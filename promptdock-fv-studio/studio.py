"""PromptDock × FreeVideo Studio. Local-first prompt manager; never changes FreeVideo output.
Independent companion application; export/import PromptDock JSON v1 (+ galleryImages extension).
GPL-3.0-or-later.
"""
import base64, json, os, shutil, time, uuid, mimetypes, subprocess, re, stat, math
from send2trash import send2trash
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog as fd, messagebox as mb, simpledialog as sd
from PIL import Image, ImageTk

ROOT=Path(os.environ.get("LOCALAPPDATA") or Path.home())/"PromptDock-FreeVideo-Studio"
DB=ROOT/"library.json"
PHOTOS=ROOT/"images"
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


def pics(row):
    out=[]
    for item in [row.get("preview",""),*row.get("images",[])]:
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
        self.selected=None;self.group="Все";self.keep=[];self.gallery=[]
        self.title("PromptDock × FreeVideo Studio  |  Windows")
        self.geometry("1370x800");self.minsize(1060,620)
        self.configure(bg="#121c2b")
        st=ttk.Style();st.theme_use("clam")
        st.configure("Treeview",background="#1c2b40",foreground="#edf2fb",fieldbackground="#1c2b40",rowheight=30,font=("Segoe UI",10))
        st.map("Treeview",background=[("selected","#355ca2")])
        st.configure("Treeview.Heading",background="#30445d",foreground="white")
        self.build();self.fill()
        self.protocol("WM_DELETE_WINDOW",self.quit_app)
        self.bind("<Control-s>",lambda e:self.save())
        if self.db.get("output"):self.after(400,self.scan)
        # Read-only background refresh: discover new FreeVideo outputs without a daemon.
        self.after(120000,self.auto_scan)

    def btn(self,parent,label,cmd,primary=False):
        return tk.Button(parent,text=label,command=cmd,bg="#4474c2" if primary else "#293d55",
            fg="white",activeforeground="white",activebackground="#4267a2",relief="flat",
            font=("Segoe UI",10),cursor="hand2",padx=9,pady=6)

    def build(self):
        top=tk.Frame(self,bg="#162235",pady=9,padx=10);top.pack(fill="x")
        tk.Label(top,text="PROMPTDOCK  ×  FREEVIDEO",bg="#162235",fg="#a6caff",
            font=("Segoe UI",14,"bold")).pack(side="left",padx=(0,13))
        for name,cmd in [("📂 output",self.pick_output),("⟳ Обновить",self.scan),
            ("＋ Промпт",self.new),("⬆ Импорт JSON",self.import_json),
            ("⬇ Экспорт в PromptDock",self.export_json),("LoRA",self.lora_window)]:
            self.btn(top,name,cmd,name=="📂 output").pack(side="left",padx=3)
        self.search=tk.StringVar()
        ttk.Entry(top,textvariable=self.search,width=23).pack(side="right",padx=5)
        tk.Label(top,text="Поиск",bg="#162235",fg="white").pack(side="right")
        self.search.trace_add("write",lambda *_:self.fill())
        panel=tk.PanedWindow(self,orient="horizontal",bg="#111c2c",sashwidth=5,bd=0)
        panel.pack(fill="both",expand=True,padx=7,pady=7)
        left=tk.Frame(panel,bg="#1a2a3f",width=190);panel.add(left,minsize=145)
        tk.Label(left,text="КАТЕГОРИИ",bg="#1a2a3f",fg="#b5cbe8",pady=11).pack()
        self.groups=tk.Listbox(left,bg="#1a2a3f",fg="#e4efff",selectbackground="#355ca2",
            selectforeground="white",font=("Segoe UI",11),relief="flat",highlightthickness=0)
        self.groups.pack(fill="both",expand=True,padx=5,pady=3)
        self.groups.bind("<<ListboxSelect>>",self.choose_group)
        middle=tk.Frame(panel,bg="#19283c",width=330);panel.add(middle,minsize=220)
        tk.Label(middle,text="ПРОМПТЫ",bg="#19283c",fg="#abc5e7",pady=10).pack()
        scroll=ttk.Scrollbar(middle)
        self.tree=ttk.Treeview(middle,columns=("src",),show="tree headings",selectmode="extended",yscrollcommand=scroll.set)
        scroll.config(command=self.tree.yview)
        self.tree.heading("#0",text="Название");self.tree.heading("src",text="Источник")
        self.tree.column("#0",width=240,minwidth=100)
        self.tree.column("src",width=95,minwidth=60,stretch=False)
        self.tree.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y")
        self.tree.bind("<<TreeviewSelect>>",self.choose_item)
        tk.Label(middle,text="Ctrl / Shift — выбор нескольких роликов",bg="#19283c",fg="#9fb6d7",font=("Segoe UI",9)).pack(fill="x",side="bottom",pady=4)
        deletion=tk.Frame(middle,bg="#19283c")
        deletion.pack(fill="x",side="bottom",padx=5,pady=5)
        self.btn(deletion,"В корзину",lambda:self.delete_selected(False)).pack(side="left",fill="x",expand=True,padx=2)
        tk.Button(deletion,text="Удалить навсегда",command=lambda:self.delete_selected(True),
            bg="#723341",fg="white",activeforeground="white",activebackground="#9e394b",
            relief="flat",font=("Segoe UI",9),cursor="hand2",padx=8,pady=7).pack(side="left",fill="x",expand=True,padx=2)
        right=tk.Frame(panel,bg="#1b293d",padx=12,pady=10);panel.add(right,minsize=450)
        line=tk.Frame(right,bg="#1b293d");line.pack(fill="x")
        self.titlevar=tk.StringVar()
        ttk.Entry(line,textvariable=self.titlevar,font=("Segoe UI",12)).pack(side="left",fill="x",expand=True)
        self.star=self.btn(line,"☆",self.favorite);self.star.pack(side="left",padx=5)
        self.btn(line,"Сохранить",self.save,True).pack(side="left")
        row=tk.Frame(right,bg="#1b293d",pady=8);row.pack(fill="x")
        tk.Label(row,text="Категория",bg="#1b293d",fg="white").pack(side="left")
        self.catvar=tk.StringVar();ttk.Entry(row,textvariable=self.catvar,width=23).pack(side="left",padx=6)
        tk.Label(row,text="Теги",bg="#1b293d",fg="white").pack(side="left")
        self.tagvar=tk.StringVar();ttk.Entry(row,textvariable=self.tagvar).pack(side="left",fill="x",expand=True,padx=6)
        tk.Label(right,text="ПРОМПТ • редактор (FreeVideo остаётся без изменений)",bg="#1b293d",fg="#aac1e2",anchor="w").pack(fill="x",pady=6)
        self.body=tk.Text(right,wrap="word",height=12,bg="#0d1929",fg="#f1f6ff",
            insertbackground="white",relief="flat",font=("Consolas",10),padx=9,pady=9)
        self.body.pack(fill="both",expand=True)
        controls=tk.Frame(right,bg="#1b293d",pady=8);controls.pack(fill="x")
        for label,cmd in [("📋 Копировать",self.copy),("▶ Видео",self.video),
            ("📂 Папка",self.open_folder),("⧉ Дубликат",self.duplicate)]:
            self.btn(controls,label,cmd).pack(side="left",padx=3)
        self.info=tk.Label(right,text="",bg="#1b293d",fg="#afc5e7",justify="left",anchor="w",
            wraplength=620,font=("Segoe UI",10))
        self.info.pack(fill="x",pady=2)
        gallery=tk.Frame(right,bg="#101e30",padx=5,pady=6);gallery.pack(fill="x",pady=(9,2))
        header=tk.Frame(gallery,bg="#101e30");header.pack(fill="x")
        tk.Label(header,text="МИНИ-ГАЛЕРЕЯ / ПРЕВЬЮ",bg="#101e30",fg="#b8cee9").pack(side="left",padx=5)
        self.btn(header,"＋ Фото",self.add_photo).pack(side="right",padx=3)
        self.btn(header,"Удалить своё фото",self.del_photo).pack(side="right",padx=3)
        self.frames=tk.Frame(gallery,bg="#101e30",height=112)
        self.frames.pack(fill="x",pady=4);self.frames.pack_propagate(False)
        self.status=tk.Label(self,text="Выбери output FreeVideo — исходные файлы никогда не изменяются.",
            bg="#111b2b",fg="#a1bfdf",anchor="w",padx=12,pady=7)
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
        q=self.search.get().strip().casefold()
        for it in sorted(self.db["items"].values(),key=lambda a:(not a.get("favorite",False),-a.get("createdAt",0))):
            g=self.group
            if g=="★ Избранное" and not it.get("favorite"):continue
            if g=="FreeVideo" and it.get("source")!="FreeVideo":continue
            if g=="Мои" and it.get("source")=="FreeVideo":continue
            if g not in ("Все","★ Избранное","FreeVideo","Мои") and g!=it.get("category"):continue
            if q and q not in (" ".join([it.get("title",""),it.get("promptEdit",it.get("prompt","")),it.get("tags","")," ".join(it.get("loras",[]))])).casefold():continue
            name=("★ " if it.get("favorite") else "")+it.get("title","Без названия")
            self.tree.insert("",tk.END,iid=it["id"],text=name[:80],values=(it.get("source","Мой"),))
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
        self.info.config(text=f"Источник: {it.get('source','Мой')}    Seed: {it.get('seed','—')}    "+
           f"Видео: {geo.get('width','?')}×{geo.get('height','?')}  {geo.get('seconds','?')} c\n"+
           "LoRA: "+(", ".join(it.get("loras",[])) or "в метаданных не указана"))
        self.show_gallery()

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
        if path:self.db["output"]=path;self.persist();self.scan()

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
        self.status.config(text=f"Проверено {len(rows)} видео. Новых промптов: {new}. Файлы FreeVideo не изменены.")

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
            self.show_gallery()
        self.persist();self.fill()
        result=f"Удалено папок: {ok}/{folders}. Выбранный объём: {capacity}."
        if failed:result+="\nНе удалось:\n"+"\n".join(failed[:8])
        mb.showinfo("Удаление завершено",result)
        self.status.config(text=result.split("\n")[0])

    def show_gallery(self):
        for child in self.frames.winfo_children():child.destroy()
        self.keep=[];self.gallery=[]
        if not self.selected:return
        self.gallery=pics(self.db["items"][self.selected])
        for i,p in enumerate(self.gallery[:7]):
            try:
                with Image.open(p) as im:
                    im.thumbnail((132,92));photo=ImageTk.PhotoImage(im.convert("RGB"))
                self.keep.append(photo)
                tk.Button(self.frames,image=photo,relief="flat",bg="#101e30",cursor="hand2",
                    command=lambda j=i:self.zoom(j)).pack(side="left",padx=4)
            except (ValueError,OSError):pass
        if len(self.gallery)>7:self.btn(self.frames,f"+{len(self.gallery)-7}",lambda:self.zoom(7)).pack(side="left")

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
