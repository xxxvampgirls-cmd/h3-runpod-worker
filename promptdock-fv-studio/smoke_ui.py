"""Windows CI smoke test: actually create the Tk UI and install an embedded bridge."""
import tempfile
from pathlib import Path
from unittest.mock import patch
from bridge_payloads import BRIDGE_JS,BRIDGE_INIT
from studio import App

def run():
    app=App()
    app.withdraw()
    try:
        app.update_idletasks()
        with tempfile.TemporaryDirectory() as tmp:
            nodes=Path(tmp)/"ComfyUI"/"custom_nodes"
            nodes.mkdir(parents=True)
            with patch.object(app,"_choose_comfyui_nodes",return_value=nodes), \
                 patch("tkinter.messagebox.showinfo",lambda *a,**kw:None), \
                 patch("tkinter.messagebox.askyesno",return_value=True):
                app.install_bridge()
            root=nodes/"promptdock_freevideo_bridge"
            assert root.is_dir(),"No bridge installed"
            assert (root/"__init__.py").read_text(encoding="utf-8")==BRIDGE_INIT
            assert (root/"web"/"bridge.js").read_text(encoding="utf-8")==BRIDGE_JS
            assert app.bridge_folder()==root
        # Smoke the real manual prompt removal without touching FreeVideo output.
        key="manual:ui-smoke-test"
        app.db["items"][key]={
            "id":key,"title":"Smoke example","source":"Мой",
            "prompt":"example","category":"Мои","tags":"","favorite":False,
            "images":[],"createdAt":1}
        app.group="Все"
        app.fill()
        app.tree.selection_set(key)
        app.choose_item(None)
        with patch("tkinter.messagebox.askyesno",return_value=True):
            app.delete_my_prompts()
        assert key not in app.db["items"]
        assert key in app.db["promptTrash"]
        app.db["promptTrash"].pop(key,None)
        # Real Tk original-images window must open, with an image linked to the
        # completed run and no copying until its explicit UI action.
        from PIL import Image
        import tkinter as tk
        with tempfile.TemporaryDirectory() as place:
            photo=Path(place)/"original.png"
            Image.new("RGB",(16,16),(50,110,180)).save(photo)
            sample="fv:smoke-original-photo"
            app.db["items"][sample]={
                "id":sample,"source":"FreeVideo","title":"Image source smoke",
                "prompt":"Still photo","sourceOriginals":[{"name":"original.png",
                  "path":str(photo),"kind":"изображение","exists":True,"role":"first"}],
                "sourceImages":[],"imageExports":[],"images":[]}
            app.selected=sample
            existing={w for w in app.winfo_children() if isinstance(w,tk.Toplevel)}
            app.show_originals()
            created=[w for w in app.winfo_children()
                     if isinstance(w,tk.Toplevel) and w not in existing]
            assert len(created)==1,"Original images dialog did not open"
            dialog=created[0]
            views=[w for w in dialog.winfo_children() if isinstance(w,tk.Frame)]
            trees=[sub for frame in views for sub in frame.winfo_children()
                   if sub.winfo_class()=="Treeview"]
            assert trees and len(trees[0].get_children())==1,"Original not displayed"
            dialog.destroy()
            del app.db["items"][sample]
            app.selected=None
        app.update_idletasks()
    finally:
        app.destroy()

if __name__=="__main__":
    run()
    print("UI and built-in bridge installation smoke checks passed")
