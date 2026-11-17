"""User-invoked FreeVideo prepared LoRA cache review.

No scans at startup, no timers, no implicit deletion. Every action requires
manual user click and selected folders go only to Windows Recycle Bin.
"""
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog as fd, messagebox as mb
from send2trash import send2trash
from lora_cache_cleaner import scan_parent, recycle_one, recycle_all_safe, discover_parents, valid_cache_parent

BG="#0D1626"
CARD="#192940"
FG="#ECF3FF"
MUTED="#AFC5E3"
ACCENT="#AE99FF"

def gb(value):
    if value>=1024**3:return f"{value/1024**3:.2f} ГБ"
    if value>=1024**2:return f"{value/1024**2:.1f} МБ"
    return f"{value/1024:.0f} КБ"

class LoraCacheWindow(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app)
        self.app=app
        self.title("LoRA CACHE / безопасная очистка FreeVideo")
        self.geometry("1080x710")
        self.minsize(870,590)
        self.configure(bg=BG)
        self.data={}
        self.last_scan=None
        self.busy=False
        self.closed=False
        self.protocol("WM_DELETE_WINDOW",self.close)
        top=tk.Frame(self,bg=CARD,padx=23,pady=14)
        top.pack(fill="x",padx=14,pady=13)
        tk.Label(top,text="◈  LORA CACHE CONTROL",bg=CARD,fg=FG,
                 font=("Segoe UI",19,"bold")).pack(anchor="w")
        tk.Label(top,text="Только подготовленные кэши vdn-online-lora-* • оригинальные LoRA и модели не трогаем",
                 bg=CARD,fg=MUTED,font=("Segoe UI",10)).pack(anchor="w",pady=(5,0))
        selector=tk.Frame(self,bg=BG)
        selector.pack(fill="x",padx=16)
        self.path=tk.StringVar(value=str(app.db.get("loraCacheRoot","")))
        tk.Label(selector,text="ПАПКА ПОДГОТОВЛЕННОГО КЭША",
                 bg=BG,fg=MUTED,font=("Segoe UI",9,"bold")).pack(anchor="w",pady=(0,4))
        entry=ttk.Entry(selector,textvariable=self.path)
        entry.pack(side="left",fill="x",expand=True,padx=(0,7))
        self.btn(selector,"📁 Выбрать",self.choose).pack(side="left",padx=(0,5))
        self.btn(selector,"⌕ Найти",self.locate).pack(side="left",padx=(0,5))
        self.btn(selector,"⟳ Проверить",self.scan,primary=True).pack(side="left")
        self.summary=tk.Label(self,text="Ничего не сканируется автоматически. Выбери папку и нажми «Проверить».",
                              bg=BG,fg="#98CFEC",anchor="w",justify="left",
                              font=("Segoe UI",11),wraplength=1000,pady=11)
        self.summary.pack(fill="x",padx=19)
        body=tk.Frame(self,bg=BG)
        body.pack(fill="both",expand=True,padx=16,pady=6)
        headings=[
            ("#0","Папка кэша",220),
            ("nominal","Объём файлов",135),
            ("real","Может освободиться*",160),
            ("shared","Жёсткие ссылки",150),
            ("time","Последнее использование",170),
            ("status","Состояние",180)]
        self.tree=ttk.Treeview(body,columns=tuple(x[0] for x in headings[1:]),
                      show="tree headings",selectmode="extended",height=13)
        for name,label,width in headings:
            self.tree.heading(name,text=label)
            self.tree.column(name,width=width,minwidth=75,anchor="w")
        scrollbar=ttk.Scrollbar(body,command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left",fill="both",expand=True)
        scrollbar.pack(side="right",fill="y")
        self.tree.tag_configure("safe",background="#183149")
        self.tree.tag_configure("new",background="#322E42")
        self.tree.bind("<<TreeviewSelect>>",self.describe)
        self.details=tk.Label(self,
                 text="Выдели папку: увидишь, какие LoRA использовались и почему она защищена.",
                 bg=CARD,fg=MUTED,anchor="w",justify="left",wraplength=1000,
                 font=("Segoe UI",10),padx=16,pady=12)
        self.details.pack(fill="x",padx=16,pady=(6,5))
        actions=tk.Frame(self,bg=BG,pady=8)
        actions.pack(fill="x",padx=16)
        self.btn(actions,"⚡ Очистить ВСЕ безопасные",self.auto_cleanup,primary=True).pack(side="left",padx=(0,8))
        self.btn(actions,"Выбранное в Корзину",self.recycle).pack(side="left",padx=(0,8))
        self.btn(actions,"Почему остались?",self.explain_remaining).pack(side="left",padx=(0,8))
        self.btn(actions,"Открыть выбранную папку",self.reveal).pack(side="left",padx=3)
        self.btn(actions,"Закрыть",self.close).pack(side="right",padx=3)
        tk.Label(self,text="* Реально освобождаемое место рассчитано только по файлам без других жёстких ссылок. "
                 "Пока Корзина Windows не очищена, место может не освободиться. "
                 "Недавние / неизвестные / используемые кэши защищены.",
                 bg=BG,fg="#94ABC7",justify="left",wraplength=1010,
                 font=("Segoe UI",9),pady=9).pack(fill="x",padx=18,pady=(0,9))

    def btn(self,parent,text,callback,primary=False):
        return tk.Button(parent,text=text,command=callback,relief="flat",bd=0,
                 bg="#725BE1" if primary else "#253E5F",
                 fg="#FFFFFF",activebackground="#8A78F5",activeforeground="white",
                 padx=14,pady=8,font=("Segoe UI",10,"bold" if primary else "normal"),
                 cursor="hand2")

    def close(self):
        self.closed=True
        self.destroy()

    def choose(self):
        initial=self.path.get() or self.app.db.get("output") or str(Path.home())
        parent=fd.askdirectory(parent=self,title="Выбери int8-convrot с папками vdn-online-lora-*",
                               initialdir=initial if Path(initial).is_dir() else str(Path.home()))
        if parent:
            self.path.set(parent)
            self.summary.configure(text="Путь выбран. Нажми «Проверить», чтобы увидеть папки.")
            self.data={}
            self.last_scan=None
            self.tree.delete(*self.tree.get_children())

    def locate(self):
        if self.busy:return
        hints=[self.app.db.get("output",""),self.app.db.get("customNodes",""),
               self.app.db.get("loraRoot",""),self.path.get()]
        self.busy=True
        self.summary.config(text="Ищу кэш по типичным папкам. Это ручной поиск, без фонового обновления…")
        def work():
            try:
                found=discover_parents(hints=hints,seconds=9)
                error=None
            except (OSError,ValueError) as exc:
                found=[];error=str(exc)
            def done():
                self.busy=False
                if error:
                    self.summary.config(text="Ошибка поиска: "+error)
                    return
                if not found:
                    self.summary.config(text="Кэш не найден автоматически. Нажми «Выбрать» и укажи int8-convrot.")
                    return
                if len(found)==1:
                    self.path.set(str(found[0]))
                    self.summary.config(text="Найдена папка "+str(found[0])+". Нажми «Проверить».")
                    return
                self.choose_found(found)
            if not self.closed:
                try:self.after(0,done)
                except (RuntimeError,tk.TclError):pass
        threading.Thread(target=work,daemon=True,name="promptdock-lora-discovery").start()

    def choose_found(self,found):
        chooser=tk.Toplevel(self)
        chooser.title("Найденные кэши FreeVideo")
        chooser.geometry("830x360")
        chooser.configure(bg=BG)
        tk.Label(chooser,text="Выбери нужный кэш — сканирование начнётся только по кнопке «Проверить».",
                 bg=BG,fg=FG,pady=12,font=("Segoe UI",11)).pack(fill="x")
        listing=tk.Listbox(chooser,bg=CARD,fg=FG,
                           selectbackground="#6B5FC3",font=("Segoe UI",10))
        listing.pack(fill="both",expand=True,padx=16,pady=9)
        for folder in found:listing.insert("end",str(folder))
        listing.selection_set(0)
        def accept():
            picked=listing.curselection()
            if picked:
                self.path.set(listing.get(picked[0]))
                self.summary.config(text="Папка найдена. Нажми «Проверить».")
            chooser.destroy()
        self.btn(chooser,"Выбрать папку",accept,True).pack(padx=17,pady=13)
        listing.bind("<Double-1>",lambda e:accept())

    def scan(self):
        if self.busy:return
        parent=self.path.get().strip()
        if not valid_cache_parent(parent):
            mb.showwarning("Кэш LoRA",
                "Папка не распознана. Укажи непосредственно int8-convrot, "
                "int8-convert, fp8-convert или cache, внутри которой лежат vdn-online-lora-*.",
                parent=self)
            return
        self.app.db["loraCacheRoot"]=parent
        self.app.persist()
        self.busy=True
        self.summary.config(text="Идёт проверка manifest.json, жёстких ссылок и размеров…")
        def worker():
            try:
                result=scan_parent(parent)
                error=None
            except (OSError,ValueError) as exc:
                result=None;error=str(exc)
            def finish():
                self.busy=False
                if error:
                    self.summary.config(text="Ошибка: "+error)
                    return
                self.render_report(result)
            if not self.closed:
                try:self.after(0,finish)
                except (RuntimeError,tk.TclError):pass
        threading.Thread(target=worker,daemon=True,name="promptdock-lora-audit").start()


    def render_report(self,result):
        """UI updates only when the user pressed cleanup or scan."""
        self.last_scan=result
        self.data={str(i):entry for i,entry in enumerate(result["rows"])}
        self.tree.delete(*self.tree.get_children())
        for key,row in self.data.items():
            seen=datetime.fromtimestamp(row["last_used"]).strftime("%d.%m.%Y %H:%M")
            self.tree.insert("",tk.END,iid=key,text=row["name"][:29],
                 values=(gb(row["logical"]),gb(row["reclaimable"]),
                         gb(row["hardlinked"]),seen,row["status"]),
                 tags=("safe",) if row["safe"] else ("new",))
        self.summary.config(text=(
             f"Кэшей: {len(result['rows'])}. "
             f"Можно очистить: {result.get('eligible',sum(x['safe'] for x in result['rows']))}. "
             f"Размер файлов: {gb(result['logical'])}. "
             f"Оценка освобождения: {gb(result['reclaimable'])}. "
             f"Недавних / защищённых: {result['protected']}. "
             f"Неопознанных / неполных: {len(result['skipped'])}."))
        self.details.config(text="Нажми «Почему остались?», чтобы увидеть причины. "
                  "Фоновое сканирование отключено, ничего не удаляется без подтверждения.")

    def explain_remaining(self):
        """Show exact skipped names, avoiding an unexplained pile of old folders."""
        data=self.last_scan
        if data is None:
            mb.showinfo("Кэш LoRA","Сначала нажми «Проверить».",parent=self)
            return
        protected=[x for x in data["rows"] if not x["safe"]]
        unknown=data["skipped"]
        win=tk.Toplevel(self)
        win.title("Что осталось и почему")
        win.geometry("950x580")
        win.configure(bg=BG)
        tk.Label(win,text="◈  ПОЧЕМУ ПАПКИ ОСТАЛИСЬ",bg=BG,fg=FG,
                 font=("Segoe UI",17,"bold"),pady=14).pack(fill="x")
        box=tk.Text(win,bg=CARD,fg="#DFECFF",relief="flat",
                    font=("Consolas",10),wrap="word",padx=15,pady=12)
        box.pack(fill="both",expand=True,padx=18,pady=10)
        notes=[
            f"Недавно использованные / защищённые: {len(protected)}",
            f"Без полного подтверждённого manifest: {len(unknown)}",
            "",
            "ЭТИ ПАПКИ НЕ УДАЛЯЛИСЬ. ОСНОВНЫЕ МОДЕЛИ И LoRA НЕ ТРОГАЛИ.",
            "Возможные причины: использовались менее суток назад, неполная",
            "подготовка, неизвестные файлы, ссылка-junction, занятый файл.",
            "",
            "НЕДАВНИЕ (<24 часов):"]
        notes.extend(f"• {x['name']} — {x['status']} ({gb(x['logical'])})"
                     for x in protected[:200])
        notes.extend(["","НЕОПОЗНАННЫЕ / НЕПОЛНЫЕ:"])
        notes.extend(f"• {name}: {error}" for name,error in unknown[:200])
        if not protected and not unknown:
            notes.append("Защищённых папок нет.")
        box.insert("1.0","\n".join(notes))
        box.config(state="disabled")
        self.btn(win,"Закрыть",win.destroy).pack(pady=12)

    def auto_cleanup(self):
        """One-click audit + user-confirmed recycle of ALL eligible variants."""
        if self.busy:return
        parent=self.path.get().strip()
        if not valid_cache_parent(parent):
            mb.showwarning("Кэш LoRA",
                "Не найдена папка int8-convrot с кэшами. "
                "Нажми «Найти» или укажи эту папку один раз.",
                parent=self)
            return
        self.app.db["loraCacheRoot"]=parent
        self.app.persist()
        self.busy=True
        self.summary.config(text="Проверяю ВСЕ папки кэша по твоему нажатию…")
        def audit():
            try:report=scan_parent(parent);error=None
            except (OSError,ValueError) as exc:report=None;error=str(exc)
            def confirm():
                self.busy=False
                if error:
                    self.summary.config(text="Ошибка сканирования: "+error)
                    return
                self.render_report(report)
                eligible=[x for x in report["rows"] if x["safe"]]
                if not eligible:
                    mb.showinfo("Автоочистка LoRA",
                        f"Нет кэшей для безопасной очистки.\n"
                        f"Недавно использованные: {report['protected']}\n"
                        f"Неполные или неизвестные: {len(report['skipped'])}\n\n"
                        "Нажми «Почему остались?» для подробностей.",parent=self)
                    return
                if not mb.askyesno("FreeVideo закрыт?",
                    "Перед очисткой закрой FreeVideo и ComfyUI, дождись окончания генераций.\n\n"
                    "Подтверждаешь, что оба приложения закрыты?",
                    parent=self,icon="warning"):
                    return
                nominal=sum(x["logical"] for x in eligible)
                reclaim=sum(x["reclaimable"] for x in eligible)
                if not mb.askyesno("Очистить всё безопасное?",
                    f"Найдено подходящих кэшей: {len(eligible)}\n"
                    f"Размер файлов: {gb(nominal)}\n"
                    f"Возможно освободится после очистки Корзины: {gb(reclaim)}\n\n"
                    f"Защищённых: {report['protected']}\n"
                    f"Неполных / неизвестных: {len(report['skipped'])}\n\n"
                    "Отправить ВСЕ подходящие кэши в Корзину Windows?\n"
                    "Оригинальные LoRA, основные модели и готовые видео не затрагиваются.\n"
                    "При повторном использовании FreeVideo может пересоздать кэш.",
                    parent=self,icon="warning"):
                    return
                self.busy=True
                self.summary.config(text="Отправляю выбранные системой безопасные кэши в Корзину Windows…")
                def work():
                    try:result=recycle_all_safe(parent,send2trash);failure=None
                    except (OSError,ValueError) as exc:result=None;failure=str(exc)
                    def done():
                        self.busy=False
                        if failure:
                            self.summary.config(text="Ошибка: "+failure)
                            mb.showwarning("Очистка LoRA",failure,parent=self)
                            return
                        self.render_report(result["remaining"])
                        msg=(f"В Корзину Windows перенесено: {len(result['moved'])} папок.\n"
                             f"Размер файлов: {gb(result['moved_logical'])}\n"
                             f"После очистки Корзины может освободиться: {gb(result['potential_bytes'])}\n"
                             f"Осталось защищённых: {result['protected']}\n"
                             f"Неполных или неизвестных: {len(result['skipped'])}\n"
                             f"Не удалось переместить: {len(result['failed'])}\n\n"
                             "Чтобы узнать, почему остались папки, нажми «Почему остались?».")
                        mb.showinfo("Результат очистки LoRA",msg,parent=self)
                    if not self.closed:
                        try:self.after(0,done)
                        except (RuntimeError,tk.TclError):pass
                threading.Thread(target=work,daemon=True,
                                 name="promptdock-lora-clean-once").start()
            if not self.closed:
                try:self.after(0,confirm)
                except (RuntimeError,tk.TclError):pass
        threading.Thread(target=audit,daemon=True,
                         name="promptdock-lora-audit-once").start()

    def describe(self,event=None):
        rows=[self.data.get(x) for x in self.tree.selection()]
        rows=[x for x in rows if x]
        if not rows:return
        selected=rows[0]
        descriptions=", ".join(selected.get("adapters",[])) or "Не указан список адаптеров"
        self.details.config(text=f"Комбинация LoRA: {descriptions[:350]}\n"
                          f"Кэш: {selected['name']}\n"
                          "Защищено в течение 24 часов после последней записи. "
                          "Удаление только по явному подтверждению.")

    def reveal(self):
        selected=self.tree.selection()
        if not selected:return
        item=self.data.get(selected[0])
        if not item:return
        p=Path(item["path"])
        if not p.is_dir():return
        if os.name=="nt":
            subprocess.Popen(["explorer.exe",str(p)])
        else:subprocess.Popen(["xdg-open",str(p)])

    def recycle(self):
        if self.busy:return
        keys=list(self.tree.selection())
        rows=[self.data[k] for k in keys if k in self.data]
        if not rows:
            mb.showinfo("Корзина Windows","Выдели одну или несколько папок кэша.",parent=self)
            return
        protected=[x for x in rows if not x["safe"]]
        allowed=[x for x in rows if x["safe"]]
        if not allowed:
            mb.showwarning("Кэш защищён",
                "Выбранные папки ещё слишком новые, содержат неизвестные файлы "
                "или были изменены менее 24 часов назад. Ничего не перемещено.",
                parent=self)
            return
        if not mb.askyesno("Подтверждение: FreeVideo закрыт?",
                "Перед удалением закрой FreeVideo и ComfyUI, а также дождись завершения "
                "любых генераций.\n\n"
                "Ты закрыл FreeVideo/ComfyUI и хочешь продолжить?",
                parent=self,icon="warning"):
            return
        nominal=sum(x["logical"] for x in allowed)
        reclaim=sum(x["reclaimable"] for x in allowed)
        text=(f"Отправить в Корзину Windows {len(allowed)} подготовленных кэшей LoRA?\n\n"
              f"Показанный размер: {gb(nominal)}\n"
              f"Оценка будущего освобождения: {gb(reclaim)}\n"
              f"Среди выбранных новых / защищённых папок пропущено: {len(protected)}\n\n"
              "Файлы основных моделей, исходные .safetensors LoRA и видео не затрагиваются.\n"
              "Очистка корзины и восстановление — вручную через Windows.\n"
              "При повторном выборе удалённой комбинации FreeVideo может пересоздать кэш.")
        if not mb.askyesno("В Корзину Windows",text,parent=self,icon="warning"):
            return
        moved=0;reclaimed=0;errors=[]
        self.busy=True
        try:
            for row in allowed:
                try:
                    result=recycle_one(row["path"],Path(row["path"]).parent,send2trash)
                    moved+=1
                    reclaimed+=result["reclaimable"]
                    # Don't trigger a potentially expensive implicit rescan.
                    for key,item in list(self.data.items()):
                        if item["path"]==row["path"]:
                            self.data.pop(key,None)
                            if self.tree.exists(key):self.tree.delete(key)
                except (OSError,ValueError,PermissionError) as exc:
                    errors.append(f"{row['name'][:24]}: {exc}")
        finally:
            self.busy=False
        msg=(f"Перемещено в Корзину Windows: {moved}.\n"
             f"После её очистки может освободиться примерно {gb(reclaimed)}.\n"
             "Проверка списка вручную — кнопка «Проверить».")
        if errors:msg+="\nПропущено:\n"+"\n".join(errors[:5])
        mb.showinfo("Кэш LoRA",msg,parent=self)
        self.summary.config(text="Очистка завершена. Для обновления показателей нажми «Проверить».")
