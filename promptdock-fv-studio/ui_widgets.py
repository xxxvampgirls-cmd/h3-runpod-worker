"""Small, dependency-free modern controls for the dark FreeVideo companion UI."""
import tkinter as tk
from tkinter import font as tkfont

class PillButton(tk.Canvas):
    """Rounded, keyboard-accessible Canvas button with hover and focus states."""
    PALETTES = {
        "primary": ("#7656E8", "#9279FC", "#FFFFFF", "#7656E8"),
        "secondary": ("#243650", "#314967", "#E8EFFC", "#37506B"),
        "danger": ("#543044", "#783C50", "#FFDEE9", "#8B4B60"),
        "mint": ("#174C52", "#21666C", "#CFFDF4", "#2D777D")
    }
    def __init__(self, master, text, command, *, primary=False, palette=None, width=None,
                 height=38, radius=16):
        bg = master.cget("bg") if "bg" in master.keys() else "#111D30"
        super().__init__(master,bg=bg,highlightthickness=0,bd=0,relief="flat",
                         takefocus=1,cursor="hand2",height=height,width=width or 100)
        self._text = str(text)
        self._command = command
        self._height = height
        self._radius = radius
        self._palette = palette or ("primary" if primary else "secondary")
        self._hover=False;self._pressed=False
        self._font=tkfont.Font(family="Segoe UI",size=10,weight="bold" if primary else "normal")
        self._min_width = width or max(40,self._font.measure(self._text)+26)
        self.configure(width=self._min_width)
        self.bind("<Configure>",lambda e:self._draw())
        self.bind("<Enter>",lambda e:self._set_hover(True))
        self.bind("<Leave>",lambda e:self._set_hover(False))
        self.bind("<Button-1>",self._click)
        self.bind("<Return>",self._click)
        self.bind("<space>",self._click)
        self.bind("<FocusIn>",lambda e:self._draw())
        self.bind("<FocusOut>",lambda e:self._draw())
        self._draw()

    def _set_hover(self, state):
        self._hover=state
        self._draw()

    def _click(self, e=None):
        if self._command is not None and getattr(self,"_enabled",True):
            self._command()

    def configure(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self._text=str(kwargs.pop("text"))
            if hasattr(self,"_font"):
                self._min_width=max(40,self._font.measure(self._text)+26)
                kwargs.setdefault("width",self._min_width)
        if "state" in kwargs:
            self._enabled=kwargs.pop("state") != "disabled"
        result=super().configure(cnf,**kwargs)
        if hasattr(self,"_font"):self._draw()
        return result

    config=configure

    def _draw(self):
        if not hasattr(self,"_font"):return
        self.delete("all")
        w=max(self.winfo_width(),self._min_width)
        h=max(self.winfo_height(),self._height)
        r=min(self._radius,h//2,w//2)
        base,hover,text,border=self.PALETTES[self._palette]
        fill=hover if self._hover else base
        if not getattr(self,"_enabled",True):fill="#394354";text="#77899D"
        # Smooth curved ends: two semicircular caps joined by a rectangle.
        self.create_oval(1,1,2*r,h-1,fill=fill,outline=fill)
        self.create_oval(w-2*r-1,1,w-1,h-1,fill=fill,outline=fill)
        self.create_rectangle(r,1,w-r,h-1,fill=fill,outline=fill)
        if self.focus_get() is self:
            self.create_line(r,1,w-r,1,fill="#C1B0FF",width=2)
        self.create_text(w/2,h/2,text=self._text,fill=text,font=self._font,anchor="center")
