"""A small in-app Windows MP4 player powered by the already-bundled FFmpeg.

Tk renders the frames inside a PromptDock-owned window, not an external app.
Windows winsound provides PCM WAV playback for the original audio track.
Nothing is written in FreeVideo's output or input directories.
"""
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageTk

FPS=24
WIDTH=720
HEIGHT=404
FRAME_BYTES=WIDTH*HEIGHT*3
BG="#0D1524"
CARD="#14243A"
CREATIONFLAGS=getattr(subprocess,"CREATE_NO_WINDOW",0)

def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def probe(path):
    """No ffprobe executable needed: FFmpeg's input banner has the duration."""
    cmd=[ffmpeg_exe(),"-hide_banner","-i",str(path)]
    try:
        result=subprocess.run(cmd,capture_output=True,text=True,
                              timeout=10,creationflags=CREATIONFLAGS)
        match=re.search(r"Duration:\s*(\d\d):(\d\d):(\d\d(?:\.\d+)?)",
                        result.stderr or "")
        return (int(match.group(1))*3600+int(match.group(2))*60+
                float(match.group(3))) if match else 0.0
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return 0.

def clock(value):
    value=max(0,int(value))
    return f"{value//60:02}:{value%60:02}" if value<3600 else (
        f"{value//3600:02}:{value//60%60:02}:{value%60:02}")

def _read_exact(stream, size):
    chunks=[]
    rest=size
    while rest:
        chunk=stream.read(rest)
        if not chunk:return None
        chunks.append(chunk)
        rest-=len(chunk)
    return b"".join(chunks)

def _stop_audio():
    if os.name=="nt":
        try:
            import winsound
            winsound.PlaySound(None,winsound.SND_PURGE)
        except (OSError,RuntimeError):
            try:winsound.PlaySound(None,0)
            except (OSError,RuntimeError):pass

class Player(tk.Toplevel):
    def __init__(self, master, video, *, title=""):
        path=Path(video)
        if not path.is_file() or path.suffix.lower() not in (".mp4",".mov",".mkv"):
            raise ValueError("Файл видео отсутствует")
        super().__init__(master)
        self.video=path
        self.duration=probe(path)
        self.position=0.
        self.is_playing=False
        self.closed=False
        self.generation=0
        self.frames=queue.Queue(maxsize=2)
        self.process=None
        self.image=None
        self.full=False
        self.scrubbing=False
        self._volume_ok=False
        self.tmp=tempfile.TemporaryDirectory(prefix="promptdock-player-")
        self.title("▶  PROMPTDOCK CINEMA  —  "+(title or path.name)[:80])
        self.geometry("840x620")
        self.minsize(760,545)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW",self.close)
        header=tk.Frame(self,bg=BG,padx=25,pady=16)
        header.pack(fill="x")
        tk.Label(header,text="◈  PROMPTDOCK  /  CINEMA",
                 bg=BG,fg="#F4F1FF",
                 font=("Segoe UI",17,"bold")).pack(side="left")
        tk.Label(header,text="ЛОКАЛЬНЫЙ ПЛЕЕР",
                 bg="#293A55",fg="#A8D9FC",padx=12,pady=5,
                 font=("Segoe UI",9,"bold")).pack(side="right")
        self.picture=tk.Label(self,bg="#060A12",
                              fg="#7A9EC3",text="Загрузка видео…",
                              font=("Segoe UI",12),relief="flat")
        self.picture.pack(fill="both",expand=True,padx=22,pady=7)
        controls=tk.Frame(self,bg=CARD,padx=16,pady=12)
        controls.pack(fill="x",padx=22,pady=(5,16))
        row=tk.Frame(controls,bg=CARD)
        row.pack(fill="x")
        self.play_button=tk.Button(row,text="❚❚  Пауза",command=self.toggle,
              bg="#7560DF",fg="white",activebackground="#9078FA",
              activeforeground="white",relief="flat",bd=0,padx=19,pady=7,
              font=("Segoe UI",11,"bold"),cursor="hand2")
        self.play_button.pack(side="left",padx=(0,11))
        self.rewind=tk.Button(row,text="↶  5 сек",command=lambda:self.seek(self.position-5),
              bg="#263B57",fg="#E4F2FF",relief="flat",bd=0,padx=11,pady=7,
              cursor="hand2")
        self.rewind.pack(side="left",padx=(0,6))
        self.forward=tk.Button(row,text="5 сек  ↷",command=lambda:self.seek(self.position+5),
              bg="#263B57",fg="#E4F2FF",relief="flat",bd=0,padx=11,pady=7,
              cursor="hand2")
        self.forward.pack(side="left")
        self.time_label=tk.Label(row,text="00:00  /  "+(clock(self.duration) if self.duration else "—"),
                     fg="#BBD6F3",bg=CARD,font=("Segoe UI",10))
        self.time_label.pack(side="left",padx=14)
        full=tk.Button(row,text="⛶  Полный экран",command=self.toggle_full,
              bg="#243750",fg="#D7ECFF",relief="flat",bd=0,padx=12,pady=7,cursor="hand2")
        full.pack(side="right")
        self.seek_var=tk.DoubleVar(value=0)
        self.scale=tk.Scale(controls,orient="horizontal",from_=0,to=max(self.duration,1),
               resolution=0.01,variable=self.seek_var,showvalue=False,
               bg=CARD,fg="#7ACFFF",troughcolor="#29486B",
               activebackground="#826EF2",highlightthickness=0,bd=0,length=600)
        self.scale.pack(fill="x",pady=(9,1))
        self.scale.bind("<ButtonPress-1>",self._scrub_start)
        self.scale.bind("<ButtonRelease-1>",self._scrub_end)
        self.bind("<space>",lambda e:self.toggle())
        self.bind("<Left>",lambda e:self.seek(self.position-5))
        self.bind("<Right>",lambda e:self.seek(self.position+5))
        self.bind("<Escape>",lambda e:self.toggle_full(False) if self.full else self.close())
        self.after(75,self._draw)
        self.after(150,self._start)

    def _start(self):
        if self.closed:return
        self._stop()
        self.generation+=1
        generation=self.generation
        self.frames=queue.Queue(maxsize=2)
        self.is_playing=True
        self.play_button.config(text="❚❚  Пауза")
        beginning=self.position
        worker=threading.Thread(target=self._decode,args=(generation,beginning),
                                daemon=True,name="PromptDock-FFmpeg")
        worker.start()

    def _decode(self,generation,beginning):
        ff=ffmpeg_exe()
        # PCM WAV is a temporary sound track; never touch FreeVideo's files.
        sound=Path(self.tmp.name)/f"audio-{generation}.wav"
        if os.name=="nt":
            try:
                command=[ff,"-hide_banner","-loglevel","error","-y",
                         "-ss",str(beginning),"-i",str(self.video),
                         "-vn","-ac","2","-ar","44100","-c:a","pcm_s16le",
                         "-t",str(min(14400,max(1,self.duration-beginning if self.duration else 3600))),
                         str(sound)]
                p=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     timeout=15,creationflags=CREATIONFLAGS)
                if p.returncode!=0 or not sound.is_file() or sound.stat().st_size<100:
                    sound=None
            except (OSError,ValueError,subprocess.TimeoutExpired):
                sound=None
        else:
            sound=None
        if self.closed or generation!=self.generation:return
        cmd=[ff,"-hide_banner","-loglevel","error","-ss",f"{beginning:.3f}",
             "-i",str(self.video),"-an",
             "-vf",f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                    f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,format=rgb24",
             "-f","rawvideo","-pix_fmt","rgb24","-"]
        try:
            p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL,bufsize=FRAME_BYTES*2,
                               creationflags=CREATIONFLAGS)
        except OSError:return
        if self.closed or generation!=self.generation:
            p.terminate()
            return
        self.process=p
        if sound is not None:
            try:
                import winsound
                winsound.PlaySound(str(sound),
                      winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
            except (OSError,RuntimeError):pass
        try:
            while not self.closed and generation==self.generation:
                frame=_read_exact(p.stdout,FRAME_BYTES)
                if frame is None:break
                while not self.closed and generation==self.generation:
                    try:
                        self.frames.put((generation,frame),timeout=.1)
                        break
                    except queue.Full:continue
        finally:
            try:p.stdout.close()
            except (OSError,AttributeError):pass
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=.4)
                except (OSError,subprocess.TimeoutExpired):p.kill()
            if not self.closed and generation==self.generation:
                # Mark actual end to avoid replaying last frame indefinitely.
                try:self.after(0,lambda:self._ended(generation))
                except (RuntimeError,tk.TclError):pass

    def _ended(self,generation):
        if self.closed or generation!=self.generation:return
        if not self.frames.empty():
            self.after(80,lambda:self._ended(generation))
            return
        self.is_playing=False
        self.play_button.config(text="▶  Повторить")
        _stop_audio()

    def _draw(self):
        if self.closed:return
        if self.is_playing:
            try:
                generation,frame=self.frames.get_nowait()
                if generation==self.generation:
                    photo=ImageTk.PhotoImage(Image.frombytes("RGB",(WIDTH,HEIGHT),frame))
                    self.image=photo
                    self.picture.config(image=photo,text="")
                    self.position+=1/FPS
                    if not self.scrubbing:self.seek_var.set(min(self.duration or self.position,self.position))
                    self.time_label.config(text=f"{clock(self.position)}  /  "+
                        (clock(self.duration) if self.duration else "—"))
            except queue.Empty:pass
        self.after(1000//FPS,self._draw)

    def _stop(self):
        self.generation+=1
        self.is_playing=False
        _stop_audio()
        proc=self.process
        self.process=None
        if proc and proc.poll() is None:
            try:proc.terminate()
            except OSError:pass

    def toggle(self):
        if self.is_playing:
            self._stop()
            self.play_button.config(text="▶  Продолжить")
        else:
            if self.duration and self.position>=self.duration-.2:
                self.position=0
            self._start()

    def seek(self,at):
        if self.closed:return
        limit=max(0,(self.duration or 0)-.2)
        self.position=max(0,min(float(at),limit)) if self.duration else max(0,float(at))
        self.seek_var.set(self.position)
        self.time_label.config(text=f"{clock(self.position)}  /  "+
            (clock(self.duration) if self.duration else "—"))
        was=self.is_playing
        self._stop()
        if was:self._start()
        else:self.play_button.config(text="▶  Продолжить")

    def _scrub_start(self,event):
        self.scrubbing=True

    def _scrub_end(self,event):
        self.scrubbing=False
        self.seek(self.seek_var.get())

    def toggle_full(self,request=None):
        self.full=not self.full if request is None else bool(request)
        self.attributes("-fullscreen",self.full)

    def close(self):
        if self.closed:return
        self.closed=True
        self._stop()
        try:self.tmp.cleanup()
        except OSError:pass
        self.destroy()
