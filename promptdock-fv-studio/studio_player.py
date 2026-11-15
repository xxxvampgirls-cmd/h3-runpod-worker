"""Integrated Tk video player with SDL audio through ffpyplayer.

No external Windows video application is launched. All media stays local.
"""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

class VideoPlayer(tk.Toplevel):
    def __init__(self, master, filename):
        super().__init__(master)
        self.filename = str(Path(filename))
        self.title("PromptDock • Видеоплеер — " + Path(filename).name)
        self.geometry("980x690")
        self.minsize(490, 360)
        self.configure(bg="#0D1523")
        self.player = None
        self._job = None
        self._photo = None
        self._paused = False
        self._seeking = False
        self._duration = 0.0
        self._closed = False
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.view = tk.Label(self, bg="#080D16", fg="#A9BDD8",
                             text="Загрузка видео…", font=("Segoe UI", 12))
        self.view.pack(fill="both", expand=True, padx=12, pady=(12, 5))
        tools = tk.Frame(self, bg="#13233A", pady=10, padx=14)
        tools.pack(fill="x", padx=12, pady=(5, 12))
        self.play_btn = tk.Button(tools, text="⏸  Пауза", command=self.toggle_pause,
                                  bg="#634DD5", fg="white", activebackground="#8069EF",
                                  activeforeground="white", borderwidth=0, padx=13, pady=6)
        self.play_btn.pack(side="left")
        tk.Button(tools, text="↺", command=self.restart, bg="#223957", fg="#E3EEFF",
                  borderwidth=0, padx=12, pady=6).pack(side="left", padx=7)
        self.position = tk.DoubleVar(value=0)
        self.slider = tk.Scale(tools, from_=0, to=1, variable=self.position, orient="horizontal",
                               showvalue=False, resolution=0.1, bg="#13233A", fg="white",
                               troughcolor="#425D81", highlightthickness=0, borderwidth=0)
        self.slider.pack(side="left", fill="x", expand=True, padx=9)
        self.slider.bind("<ButtonPress-1>", self.begin_seek)
        self.slider.bind("<ButtonRelease-1>", self.end_seek)
        self.timestamp = tk.Label(tools, text="00:00 / --:--", bg="#13233A",
                                  fg="#C4D8F7", font=("Consolas", 10))
        self.timestamp.pack(side="left", padx=8)
        self.mute_btn = tk.Button(tools, text="🔊", command=self.toggle_mute,
                                  bg="#223957", fg="#E3EEFF", borderwidth=0, padx=10, pady=6)
        self.mute_btn.pack(side="left")
        self.bind("<space>", lambda event: self.toggle_pause())
        self.bind("<Escape>", lambda event: self.close())
        try:
            from ffpyplayer.player import MediaPlayer
            self.player = MediaPlayer(self.filename, ff_opts={
                "out_fmt": "rgb24", "sync": "audio", "x": 1024, "y": -1, "volume": 0.8})
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            self.view.configure(text="Встроенное воспроизведение недоступно.")
            messagebox.showerror("Видеоплеер", f"Не удалось открыть видео:\n{error}", parent=self)
            self.after(1, self.close)
            return
        self._schedule(25)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds or 0))
        return f"{seconds//60:02d}:{seconds%60:02d}"

    def _schedule(self, delay):
        if not self._closed:
            self._job = self.after(max(10, min(200, int(delay))), self._tick)

    def _tick(self):
        self._job = None
        if self._closed or not self.player:
            return
        try:
            meta = self.player.get_metadata()
            duration = meta.get("duration") or 0
            if duration and duration > 0:
                self._duration = float(duration)
                self.slider.configure(to=self._duration)
            pts = self.player.get_pts()
            if pts is not None and pts >= 0 and not self._seeking:
                self.position.set(min(float(pts), self._duration or float(pts)))
            self.timestamp.configure(text=f"{self._format_time(self.position.get())} / "
                                          f"{self._format_time(self._duration) if self._duration else '--:--'}")
            frame, wait = self.player.get_frame()
            if wait == "eof":
                self._paused = True
                self.play_btn.configure(text="▶  Снова")
                return
            if frame is not None:
                image, _ = frame
                width, height = image.get_size()
                if width > 0 and height > 0:
                    rgb = Image.frombytes("RGB", (width, height), bytes(image.to_bytearray()[0]))
                    max_w = max(100, self.view.winfo_width()-8)
                    max_h = max(100, self.view.winfo_height()-8)
                    rgb.thumbnail((max_w, max_h), Image.Resampling.BILINEAR)
                    self._photo = ImageTk.PhotoImage(rgb)
                    self.view.configure(image=self._photo, text="")
            self._schedule(wait*1000 if isinstance(wait, (int, float)) and wait>0 else 18)
        except (OSError, RuntimeError, ValueError) as error:
            self.view.configure(image="", text=f"Ошибка воспроизведения: {error}")
            self._schedule(150)

    def toggle_pause(self):
        if not self.player: return
        if self._paused and self._duration and self.position.get() >= self._duration-0.2:
            self.restart()
            return
        self._paused = not self._paused
        self.player.set_pause(self._paused)
        self.play_btn.configure(text="▶  Играть" if self._paused else "⏸  Пауза")
        if not self._paused and self._job is None:
            self._schedule(12)

    def restart(self):
        if not self.player: return
        self.player.seek(0, relative=False, accurate=False)
        self._paused = False
        self.player.set_pause(False)
        self.play_btn.configure(text="⏸  Пауза")
        if self._job is None: self._schedule(12)

    def begin_seek(self, event=None):
        self._seeking = True

    def end_seek(self, event=None):
        if not self.player: return
        self._seeking = False
        point = self.position.get()
        self.player.seek(point, relative=False, accurate=False)
        if self._job is None and not self._paused:
            self._schedule(12)

    def toggle_mute(self):
        if not self.player: return
        next_state = not self.player.get_mute()
        self.player.set_mute(next_state)
        self.mute_btn.configure(text="🔇" if next_state else "🔊")

    def close(self):
        if self._closed:return
        self._closed = True
        if self._job is not None:
            try: self.after_cancel(self._job)
            except tk.TclError: pass
            self._job = None
        if self.player:
            try: self.player.close_player()
            except (RuntimeError, OSError): pass
            self.player = None
        self.destroy()
