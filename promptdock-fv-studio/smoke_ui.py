"""Windows CI smoke test: actually create the Tk UI and install an embedded bridge."""
import tempfile
from pathlib import Path
from unittest.mock import patch
from bridge_embedded import BRIDGE_JS,BRIDGE_PY
from studio import App

def run():
    app=App()
    app.withdraw()
    try:
        app.update_idletasks()
        with tempfile.TemporaryDirectory() as tmp:
            nodes=Path(tmp)/"ComfyUI"/"custom_nodes"
            nodes.mkdir(parents=True)
            with patch("tkinter.messagebox.showinfo",lambda *a,**kw:None):
                if not app._install_bridge_to(nodes,parent=app):
                    raise AssertionError("Bridge installation declined or failed")
            root=nodes/"promptdock_freevideo_bridge"
            assert (root/"__init__.py").read_text(encoding="utf-8")==BRIDGE_PY
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
        app.update_idletasks()
    finally:
        app.destroy()

if __name__=="__main__":
    run()
    print("UI and built-in bridge installation smoke checks passed")
